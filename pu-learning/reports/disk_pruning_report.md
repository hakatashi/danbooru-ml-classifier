# 古い画像の自動削除システム — 実装・運用レポート

作成日: 2026-09-07

## 1. 概要

`/mnt/cache2/danbooru-ml-classifier/images` のディスク逼迫(空き容量423GB、使用率88%)を解消するため、30日以上前の画像について「日ごとに約10%だけを残し、残りのファイルを削除してMongoDBのメタ情報は保持する」自動削除システム(`worker/prune_old_images.py`)を実装した。recommendation改善プラン(`pu-learning/reports/recommendation_improvement_plan.md`)の特徴量ストアバックフィルと衝突しないよう安全策を組み込み、数日規模の先行バックフィルスプリントを経て全期間(30日超・約121万件)に対して実削除を実行した。結果、空き容量は312GB→2.9TBまで回復した。

## 2. 事前調査で得られた数値

### 2.1 着手時点のディスク状況
- 総容量 3.6TB、使用 3.0TB(88%)、空き 423GB
- `images/` の内訳: pixiv 1.3TB / gelbooru 640GB / sankaku 717GB / danbooru 380GB

### 2.2 recommendation改善プランとの衝突リスク
- 特徴量ストア(`worker/feature_store.py`, HDF5)への画像バックフィルが進行中だったが、**着手時点で30日超の画像135万件のうち`features.stored=true`はわずか4.7万件(3.5%)**しかなかった。
- 画像ファイルを削除すると元画像が必要な特徴抽出が二度とできなくなるため、削除は`features.stored=true`の画像のみに限定する設計とした。

### 2.3 バックフィル所要時間の見積もりと実測
- `recommendation_improvement_plan.md`記載の「閲覧済み22,804件=数時間」から逆算したスループット見積もり: 約1.5〜3枚/秒
- これをもとに135万件全件の先行バックフィルは**GPU連続占有で5〜15日**と見積もった(常駐Slackbot llama-serverの停止を伴う)
- 実際にユーザーがバックフィルを開始した際のログから実測したスループットは**約3.9枚/秒**(見積もりよりやや速い)。これにより全体所要時間の見積もりを**約3.2〜3.3日(約78時間)**に下方修正した
- 最終的に数日かけて実施され、`features.stored=true`は着手時4.7万件 → **1,362,717件**まで到達し、30日超画像のカバレッジは**実質100%**(1,167,314件中1,167,093件)に達した

### 2.4 保持アルゴリズムの検証実験
- テスト期間(2026-03-22〜04-20、30日間、対象230,455件)でのdry-run結果:
  - 削除対象 214,140〜214,252件(重みチューニング前後)
  - 保持対象 16,315件(実効保持率 約7.1%、目標10%よりやや低いのはタブ間の重複除去による目減り)
- タブ別の「保証選出件数(target_k)」内訳(初期重み: Gemini×5, Libra×3, 他×1、合計13):
  | タブ | 重み | target_k合計(30日) | 他タブと非重複の単独寄与 |
  |---|---:|---:|---:|
  | Gemini | 5 | 7,081 | 3,653 |
  | Libra | 3 | 4,248 | 853 |
  | Aries | 1 | 1,416 | 667 |
  | Taurus | 1 | 1,416 | 1,082 |
  | Cancer | 1 | 1,416 | 421 |
  | Leo | 1 | 1,416 | 827 |
  | Virgo | 1 | 1,416 | 30 |
- Virgoは他タブ(特にGemini/Cancer/Leo/Libraの構成モデル)との重複が大きく単独寄与がごく僅か(30件)だった一方、Taurus/Aries/Leoは(twitter学習モデル中心のため)独自性が高いことが判明。この結果を踏まえ、ユーザーの判断でAriesの重みを1→2に引き上げ、Geminiを5→4に引き下げた(合計重み13は不変)。調整後のtarget_k合計はGemini 5,667件・Aries 2,831件などに変化。

### 2.5 並行開発との調整
- 実装期間中に別セッションで stage-2 re-ranker(LightGBM, `worker/reranker.py`, コミット`6911edc`)が追加され、`public/src/config/namedSorts.ts`に8番目のタブ「Scorpio」(`inferences.reranker_v1.score`)が加わった。
- Scorpioは日次のLibra上位500件のみを再スコアする性質上、既存7タブと同列の比例配分には馴染まないと判断し、`TAB_WEIGHTS`には含めず、「Scorpioソート順で上位3ページ(150件)は常に保持」という**絶対保護ルール**として別途実装した(favoritesと同じ「quota計算前の強制キープ」扱い)。

## 3. 実装内容

| 種別 | ファイル | 内容 |
|---|---|---|
| 新規 | `worker/prune_old_images.py` | 日次の保持アルゴリズム本体。CLI: `--dry-run` / `--older-than-days` / `--date-from` / `--date-to` |
| 変更 | `worker/main.py` | 既存のGPU推論バッチの末尾に、`features.stored`未完了の最古画像を`PRUNE_BACKFILL_BATCH_SIZE`(既定2000件/日)ずつ段階的にバックフィルするステップを追加。既存のcontrollerスケジュール(05:00 JST)に相乗りし、独立timerは追加しない |
| 新規 | `worker/systemd/danbooru-prune.{service,timer}`, `install-prune.sh` | 日次06:00 JSTの自動削除timer一式(**現時点で未インストール**、下記5.1参照) |
| 新規 | `public/src/utils/placeholderImage.ts` | 削除済み画像の共有プレースホルダー(SVG data URI)と`@error`フォールバック |
| 変更 | `public/src/api/mlApi.ts` | `ApiImageDocument.imageDeleted`追加、`getImageSrc()`新設(削除済みなら無駄なリクエストを送らずプレースホルダーを返す) |
| 変更 | `DailyRecommendationView.vue` / `JustifiedGallery.vue` / `ArchivesView.vue` / `ImageCard.vue` | 全`<img>`にプレースホルダー・フォールバックを適用 |
| 変更 | `CLAUDE.md`, `recommendation_improvement_plan.md` | スクリプト・フィールド・優先順位の追記 |

### 保持アルゴリズム(最終版)
日付ごとに以下を強制キープ(quota計算前に確保):
1. お気に入り画像
2. `features.stored`未完了の画像(バックフィル未済 — 改善プランとの非衝突の核心)
3. Scorpioタブ上位150件(3ページ分)

残りのquota(`ceil(その日の件数 × 10%)`から強制キープ数を差し引いた分)を、7つの黄道十二宮タブの重み付きランク和集合で選出:
`Gemini×4, Libra×3, Aries×2, Taurus/Cancer/Leo/Virgo×1`(合計13)

MongoDB更新: `localPath: null, imageDeleted: true, imageDeletedAt: <日時>`。`inferences` / `favorites` / `key`等のメタ情報は一切変更しない。

## 4. 実行結果

- テスト実行(2026-03-22〜04-20)で問題ないことを確認後、**全期間(30日超、約121万件)に対して実削除を実行**
- 実績: 対象1,214,412件(非dedup)のうち **1,086,106件を削除(89.4%)**、81,433件が生存(6.7%、合計192.4GB)
- ディスク空き容量: **312GB → 2,911.9GB(2.9TB)**に回復(使用率 88%超 → 23%)

## 5. 今後の見通し

### 5.1 重要な前提: 自動timerが未インストール
`worker/systemd/danbooru-prune.timer`は**現時点でインストール・有効化されていない**(`systemctl --user is-enabled`で`not-found`)。これまでの削除は全て手動実行。以下の予測はこの状態を前提に2パターン提示する。

### 5.2 直近30日間の流入実績
- 平均 **7,774枚/日、19.11GB/日**(平均ファイルサイズ約2.4MB)

### 5.3 枯渇予測

| シナリオ | 想定 | 枯渇までの目安 |
|---|---|---|
| **自動timerを稼働させた場合** | 30日を超えた画像は毎日約6.7%まで自動的に削減され続ける。直近30日ぶんのフルサイズ画像(約573GB)はローリングウィンドウとしてほぼ一定、それより古い「保持テール」が実効保持率6.7%相当のペース(約1.28GB/日)でのみ緩やかに増加 | 現在の空き2,911.9GBに対し **約2,274日 ≈ 6.2年** |
| **手動運用のまま二度と実行しない場合** | 削除が一切走らないため19.11GB/日のフル流入速度でそのまま埋まる | **約152日 ≈ 5.1ヶ月** |

**結論・推奨**: 現在の設計は「30日を超えた画像の90%超を継続的に削減し続ける」前提で初めて長期的な効果(約6年の runway)を発揮する。**`bash worker/systemd/install-prune.sh` によるtimerのインストールを強く推奨する**(GPU不要のCPU/ディスクI/O処理のみなので、常駐アプリのスケジュールと競合しない)。timerを入れないまま放置すると、実質的には数ヶ月単位で再び容量逼迫に戻る。

## 6. 未完了・フォローアップ項目

- `worker/systemd/danbooru-prune.timer` のインストール(上記5.1)
- `worker/prune_old_images.py` / `CLAUDE.md` へのScorpio保護・重み調整(Gemini 5→4, Aries 1→2)は**未コミット**(作業ツリー上の変更のみ)。コミットする場合は別途指示が必要
- `features.stored`のカバレッジは30日超画像でほぼ100%に到達したが、直近30日以内の新着画像は今後main.pyの段階的トップアップ(`PRUNE_BACKFILL_BATCH_SIZE`既定2000件/日)で追いついていく想定。流入ペース(約7,774件/日)に対してこのバッチサイズで追従できているかは、しばらく運用してから`features.stored`のカバレッジ推移を確認するのが望ましい
- サムネイル(`matrix-images.hakatashi.com`側)はこのリポジトリの管理外のため、削除後にサムネイルがどう振る舞うかは未検証。Web側のプレースホルダー実装はサムネイル404も想定した設計になっている

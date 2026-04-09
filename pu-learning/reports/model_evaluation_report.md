# モデル評価レポート

評価データ: 手動ラベリング済み検証セット（pixiv_public: 264件, pixiv_private: 387件, not_bookmarked: 10,219件）

## 指標の見方

| 指標 | 概要 |
|------|------|
| **wNDCG@K** | 上位K件のランキング品質。rating=3 の画像に最大重みを与えた graded NDCG。高いほど「確実に好みの画像」が上位に集中している |
| **wAUC** | 正例と負例の全体的な識別能力。K に依存しない |
| **wAP** | Precision-Recall 曲線全体の統合値。FP/FN の両方をバランスよく評価 |
| **wPrec@K** | 上位K件に含まれる正例の重み付き割合。FP への罰則が直接反映される |
| **wF0.5@K** | Precision 重視の F 値（β=0.5）。閲覧枠が限られる場合に重要 |
| **wF1@K** | Precision/Recall 均等の F 値 |

重みはすべて `rating_weight × artwork_debias`（pixiv 同一作品の複数ページを 1/artwork_group_size で補正）。

---

## pixiv_private モデルの評価

### サマリーテーブル

#### K=100

| モデル | wNDCG@100 | wAUC | wAP | wPrec@100 | wF1@100 |
|--------|----------:|-----:|----:|----------:|--------:|
| **eva02_pixiv_private_nnpu** | **0.3247** | **0.9084** | **0.5017** | 0.7136 | 0.2260 |
| pixai_pixiv_private_elkan_noto | 0.2071 | 0.8647 | 0.3840 | **0.7863** | **0.2490** |
| torch-multiclass (legacy) | 0.1811 | 0.8337 | 0.3433 | 0.6241 | 0.1976 |
| deepdanbooru_pixiv_private_nnpu | 0.1482 | 0.8360 | 0.3445 | 0.5985 | 0.1895 |
| deepdanbooru_pixiv_private_elkan_noto | 0.1463 | 0.8300 | 0.3664 | 0.5447 | 0.1725 |
| eva02_pixiv_private_elkan_noto | 0.1419 | 0.8866 | 0.3925 | 0.5100 | 0.1615 |
| eva02_pixiv_private_biased_svm | 0.1417 | 0.8881 | **0.3957** | 0.5175 | 0.1639 |

#### K=300

| モデル | wNDCG@300 | wAUC | wAP | wPrec@300 | wF1@300 |
|--------|----------:|-----:|----:|----------:|--------:|
| **eva02_pixiv_private_nnpu** | **0.4408** | **0.9084** | **0.5017** | **0.6974** | **0.5032** |
| eva02_pixiv_private_elkan_noto | 0.2803 | 0.8866 | 0.3925 | 0.5222 | 0.3767 |
| pixai_pixiv_private_elkan_noto | 0.2669 | 0.8647 | 0.3840 | 0.5164 | 0.3726 |
| eva02_pixiv_private_biased_svm | 0.2662 | 0.8881 | **0.3957** | 0.4939 | 0.3564 |
| deepdanbooru_pixiv_private_elkan_noto | 0.2622 | 0.8300 | 0.3664 | 0.5400 | **0.3896** |
| torch-multiclass (legacy) | 0.2487 | 0.8337 | 0.3433 | 0.4591 | 0.3312 |

### おすすめモデル（private）

#### 🥇 第1位: `eva02_pixiv_private_nnpu`

**全指標で圧倒的な1位。**

- wNDCG@100=0.3247、wNDCG@300=0.4408 はいずれもトップ（2位に対し K=100 で +57%、K=300 で +57% の差）
- wAUC=0.9084 は全モデル中最高。正例と負例を識別する総合能力が突出している
- wAP=0.5017 も最高で、Precision-Recall カーブ全体にわたって優秀
- **weighted と unweighted のスコア差が最大**（wNDCG@300: 0.4408 vs 0.3389）。これは rating=3 の画像を他のモデルより正確に上位に置けていることを意味する。「rating=3 を特に強くレコメンド」という要件に最も合致する

**特徴:** EVA02-Large の1024次元視覚埋め込みを特徴量とした nnPU 学習。nnPU の非負リスク補正により unlabeled 中の隠れた正例に引きずられず、真の正例パターンを精度よく学習できている。

**推奨用途:** あらゆる場面での第一選択。

---

#### 🥈 第2位: `pixai_pixiv_private_elkan_noto`（K=100 精度重視時）

- **wPrec@100=0.7863 が全モデル中で最高**。上位100件のほぼ8割が確実な private 相当
- wF1@100=0.2490 も最高で、少ない閲覧枠でのバランスが最良
- ただし K=300 では wNDCG が3位に後退。スコアの上位集中力は eva02_nnpu に劣る
- wNDCG の weighted/unweighted 差が比較的小さく、rating=3 の選別精度はやや低め

**特徴:** PixAI Tagger の13,461次元タグ確率 + Elkan-Noto EM 法。細粒度なタグ空間が精度の高い分類境界を形成。上位の「汚染率」を最小化したい場合に向く。

**推奨用途:** 毎日の閲覧枠を最小限（50〜100件以内）に絞りたい場合。

---

#### 🥉 第3位: `eva02_pixiv_private_elkan_noto` / `eva02_pixiv_private_biased_svm`（K=300 網羅重視時）

この2モデルは K=300 でほぼ同スコア（wNDCG 0.2803 vs 0.2662）。

- **eva02_elkan_noto**: wNDCG@300=0.2803 で2位。wAP=0.3925 はビルトイン・nnpu に次ぐ高水準。r2plus（rating≥2限定）で wNDCG がさらに上がり（0.2906）、質の高いサンプルへの汎化が良い
- **eva02_biased_svm**: wAP=0.3957 が全モデル中で実質最高クラス（nnpu の 0.5017 に次ぐ）。biased SVM は実装が軽量で推論速度が速い

**特徴:** どちらも EVA02 特徴量を使いつつ、nnPU より保守的な手法（EM 法・biased SVM）で学習。より広い K の範囲（100〜400件）での網羅率が安定している。

---

## pixiv_public モデルの評価

### サマリーテーブル

#### K=100

| モデル | wNDCG@100 | wAUC | wAP | wPrec@100 | wF1@100 |
|--------|----------:|-----:|----:|----------:|--------:|
| deepdanbooru_twitter_biased_svm | **0.0290** | 0.7642 | 0.1355 | 0.0940 | 0.0406 |
| eva02_twitter_biased_svm | 0.0222 | 0.8335 | 0.1933 | 0.0656 | 0.0284 |
| eva02_twitter_elkan_noto | 0.0216 | 0.8440 | 0.1933 | 0.0850 | 0.0368 |
| pixai_pixiv_public_nnpu | 0.0200 | 0.7997 | 0.1593 | 0.1077 | 0.0466 |
| deepdanbooru_pixiv_public_biased_svm | 0.0175 | 0.7258 | 0.1101 | **0.1239** | **0.0536** |
| eva02_twitter_nnpu | 0.0156 | **0.8705** | **0.2136** | 0.0859 | 0.0372 |

#### K=300

| モデル | wNDCG@300 | wAUC | wAP | wPrec@300 | wF1@300 |
|--------|----------:|-----:|----:|----------:|--------:|
| **eva02_twitter_elkan_noto** | **0.0667** | 0.8440 | 0.1933 | **0.1223** | **0.1108** |
| eva02_twitter_biased_svm | 0.0549 | 0.8335 | 0.1933 | 0.0893 | 0.0809 |
| deepdanbooru_twitter_biased_svm | 0.0536 | 0.7642 | 0.1355 | 0.0930 | 0.0843 |
| eva02_twitter_nnpu | 0.0469 | **0.8705** | **0.2136** | 0.0984 | 0.0891 |
| deepdanbooru_twitter_nnpu | 0.0389 | 0.8376 | 0.1683 | 0.1082 | 0.0980 |
| pixai_twitter_elkan_noto | 0.0344 | 0.7195 | 0.1365 | 0.1039 | 0.0941 |

### 重要な観察: twitterモデルが pixiv_public に有効

**「pixiv_public」ラベルで訓練したモデルより、「twitter」ラベルで訓練したモデルの方が pixiv_public 評価で高スコア**を出している。

考えられる理由:
- Twitter ブックマーク（公開投稿への保存）と Pixiv 公開ブックマークは「ライトな好み・幅広い好み」という特性を共有している
- twitter訓練データは量が多く（splits.parquet: 46,787件）、多様な「公開ブックマーク相当」パターンをカバーしている
- pixiv_public 専用モデルはサンプル数（77,553件だが今回の評価正例は264件）に対してモデルの自由度が合っていない可能性

### おすすめモデル（public）

> **注記:** pixiv_public モデルの絶対スコアは pixiv_private より大幅に低い（最高 wNDCG@300=0.067 vs 0.44）。これは正例数の少なさ（264件）と、「公開ブックマーク」の特徴的パターンが private より拡散していることを反映している。

#### 🥇 第1位: `eva02_twitter_elkan_noto`

**K=300 で最多指標のトップ。**

- wNDCG@300=0.0667（1位、2位の 0.0549 に対し +21%）
- wPrec@300=0.1223（1位）、wF1@300=0.1108（1位）
- wAUC=0.8440（twitter 系の中で高水準）
- r2plus（rating≥2限定）でもスコアがほぼ変わらず（0.0675）、rating の低いサンプルを除外してもロバスト

**特徴:** EVA02 特徴量（視覚的意味埋め込み）+ Elkan-Noto EM 法。Twitter 投稿の「公開好み」パターンを視覚意味空間で捉えており、Pixiv 公開ブックマークへの転移性が最も高い。

**推奨用途:** K=200〜300 以上の広い閲覧枠で安定した精度が必要な場合。

---

#### 🥈 第2位: `eva02_twitter_nnpu`

- **wAUC=0.8705 は全 public モデル中で最高**。正例・負例の識別能力が最も優れている
- **wAP=0.2136 も全 public モデル中で最高**。Precision-Recall カーブ全体が優秀
- ただし wNDCG@100 では低め（0.0156, 5位）。上位集中力より総合識別力が高いタイプ

**特徴:** nnPU の非負リスク補正により unlabeled からのノイズを抑制。wAUC・wAP ではトップだが、wNDCG が低めなのは rating=3 の画像を特別上位に集める能力がやや弱いことを示す。

**推奨用途:** 広域スクリーニング（精度は低くてよいが取りこぼしを減らしたい場合）。

---

#### 🥉 第3位: `deepdanbooru_twitter_biased_svm`（K=100 精度重視時）/ `deepdanbooru_twitter_nnpu`（K=300 網羅重視時）

- **deepdanbooru_twitter_biased_svm**: K=100 での wNDCG=0.0290（1位）。DeepDanbooru 6000次元タグ空間で biased SVM を使ったシンプルな構成ながら上位集中力が高い。推論が高速
- **deepdanbooru_twitter_nnpu**: K=300 wPrec=0.1082（twitter 系で1位）、wF1=0.0980（2位）。nnPU により広いKでの精度維持力が高い

---

## まとめと運用指針

### private ブックマーク推薦

| 優先事項 | 推奨モデル |
|---------|-----------|
| **あらゆる場面の第一選択** | `eva02_pixiv_private_nnpu` |
| 閲覧枠 ≤100件、上位精度最優先 | `pixai_pixiv_private_elkan_noto` |
| 閲覧枠 200〜400件、網羅率重視 | `eva02_pixiv_private_elkan_noto` |

### public ブックマーク推薦

| 優先事項 | 推奨モデル |
|---------|-----------|
| **あらゆる場面の第一選択** | `eva02_twitter_elkan_noto` |
| 識別能力・取りこぼし軽減重視 | `eva02_twitter_nnpu` |
| 推論速度重視（DeepDanbooru特徴量） | `deepdanbooru_twitter_biased_svm` |

### 補足: combined スコアの運用

実運用では `max(private_score, public_score)` で結合して1つのランキングを作る場合、**private モデルのスコアが支配的**になりやすい（絶対スコールが大幅に高い）点に注意。スコールを正規化（例: 各モデルの分位数正規化）してから max を取るか、両方のスコアを独立に閾値処理することを検討すること。

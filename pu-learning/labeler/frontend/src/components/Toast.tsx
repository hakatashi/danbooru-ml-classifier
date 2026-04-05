import { cx } from '../utils/cx';
import styles from './Toast.module.css';

interface Props {
  message: string;
  visible: boolean;
}

export function Toast({ message, visible }: Props) {
  return (
    <div className={cx(styles.toast, visible && styles.visible)}>
      {message}
    </div>
  );
}

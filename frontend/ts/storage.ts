export interface Storage {
  read(key: string): string | null;
  write(key: string, value: string): void;
}

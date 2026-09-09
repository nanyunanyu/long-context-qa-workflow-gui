export {};

declare global {
  interface Window {
    lcqa?: {
      apiBase: string;
      pickFolder: () => Promise<string | null>;
    };
  }
}

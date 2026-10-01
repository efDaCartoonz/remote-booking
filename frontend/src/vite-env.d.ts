/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OMNIDESK_PARENT_ORIGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

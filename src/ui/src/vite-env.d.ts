/// <reference types="vite/client" />

declare module '*?raw' {
  const content: string;
  export default content;
}

declare module 'elkjs/lib/elk.bundled.js' {
  export default class ELK {
    layout(graph: unknown): Promise<unknown>;
  }
}

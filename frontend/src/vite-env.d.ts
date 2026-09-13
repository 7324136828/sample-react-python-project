/// <reference types="vite/client" />

interface ModelContextTool {
  name: string;
  title: string;
  description: string;
  inputSchema: {
    type: 'object';
    properties: Record<string, never>;
    additionalProperties: false;
  };
  annotations: {
    readOnlyHint: boolean;
    untrustedContentHint: boolean;
  };
  execute: () => Promise<Record<string, number | string>>;
}

interface ModelContext {
  registerTool: (
    tool: ModelContextTool,
    options?: { signal?: AbortSignal },
  ) => Promise<unknown> | unknown;
}

interface Document {
  modelContext?: ModelContext;
}

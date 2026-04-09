export type Role = 'user' | 'agent' | 'system' | 'tool' | 'developer';

export interface ToolCall {
  id: string;
  type: string;
  function: {
    name: string;
    arguments: string; // JSON string
  };
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  metadata?: any;
  reasoning?: string;
  tool_calls?: ToolCall[];
}

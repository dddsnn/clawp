import { z } from 'zod';

export const Iso8601Schema = z.string().transform((str) => new Date(str));

export const StartMessageMetadataSchema = z.object({
  seq_in_session: z.number().nullable().optional(),
});

export const EndMessageMetadataSchema = z.object({
  time: Iso8601Schema,
});

export const MessageMetadataSchema = StartMessageMetadataSchema.merge(EndMessageMetadataSchema);

const BaseMessageSchema = z.object({
  metadata: MessageMetadataSchema,
  content: z.string(),
});

export const DeveloperMessageSchema = BaseMessageSchema.extend({
  role: z.literal('developer'),
});

export const SystemMessageSchema = BaseMessageSchema.extend({
  role: z.literal('system'),
});

export const ToolMessageSchema = BaseMessageSchema.extend({
  role: z.literal('tool'),
  tool_call_id: z.string(),
});

export const UserMessageSchema = BaseMessageSchema.extend({
  role: z.literal('user'),
});

export const ToolCallFunctionSchema = z.object({
  name: z.string().default(''),
  arguments: z.string().default(''),
});

export const ToolCallSchema = z.object({
  id: z.string(),
  function: ToolCallFunctionSchema,
});

export const AssistantMessageSchema = BaseMessageSchema.extend({
  role: z.literal('assistant'),
  reasoning: z.string(),
  tool_calls: z.array(ToolCallSchema),
  errors: z.array(z.string()).default([]),
});

export const NonStreamableMessageSchema = z.discriminatedUnion('role', [
  DeveloperMessageSchema,
  SystemMessageSchema,
  ToolMessageSchema,
  UserMessageSchema,
]);

export const MessageSchema = z.discriminatedUnion('role', [
  AssistantMessageSchema,
  DeveloperMessageSchema,
  SystemMessageSchema,
  ToolMessageSchema,
  UserMessageSchema,
]);

// --- Streaming Markers ---

const BaseStreamingMessageMarkerSchema = z.object({
  marker_type: z.enum(['message_start', 'message_end', 'part_start', 'part_end']),
});

export const StreamingMessageMarkerMessageStartSchema = BaseStreamingMessageMarkerSchema.extend({
  marker_type: z.literal('message_start'),
  metadata: StartMessageMetadataSchema,
});

export const StreamingMessageMarkerMessageEndSchema = BaseStreamingMessageMarkerSchema.extend({
  marker_type: z.literal('message_end'),
  metadata: EndMessageMetadataSchema,
});

export const StreamingMessageMarkerPartStartSchema = BaseStreamingMessageMarkerSchema.extend({
  marker_type: z.literal('part_start'),
  part_type: z.enum(['content', 'error', 'reasoning', 'tool']),
});

export const StreamingMessageMarkerPartEndSchema = BaseStreamingMessageMarkerSchema.extend({
  marker_type: z.literal('part_end'),
});

export const StreamingMessageMarkerSchema = z.discriminatedUnion('marker_type', [
  StreamingMessageMarkerMessageStartSchema,
  StreamingMessageMarkerMessageEndSchema,
  StreamingMessageMarkerPartStartSchema,
  StreamingMessageMarkerPartEndSchema,
]);

// --- Streaming Fragments ---

const BaseStreamingMessageFragmentSchema = z.object({
  fragment_type: z.enum(['text', 'tool_call']),
});

export const StreamingMessageFragmentTextSchema = BaseStreamingMessageFragmentSchema.extend({
  fragment_type: z.literal('text'),
  fragment: z.string(),
});

export const StreamingMessageFragmentToolCallSchema = BaseStreamingMessageFragmentSchema.extend({
  fragment_type: z.literal('tool_call'),
  fragment: ToolCallSchema,
});

export const StreamingMessageFragmentSchema = z.discriminatedUnion('fragment_type', [
  StreamingMessageFragmentTextSchema,
  StreamingMessageFragmentToolCallSchema,
]);

// --- Websocket Chunks ---

const BaseWebsocketChunkSchema = z.object({
  chunk_type: z.enum(['full_message', 'assistant_message_marker', 'assistant_message_fragment']),
});

export const WebsocketChunkFullMessageSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('full_message'),
  payload: NonStreamableMessageSchema,
});

export const WebsocketChunkAssistantMessageMarkerSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('assistant_message_marker'),
  payload: StreamingMessageMarkerSchema,
});

export const WebsocketChunkAssistantMessageFragmentSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('assistant_message_fragment'),
  payload: StreamingMessageFragmentSchema,
});

export const WebsocketChunkSchema = z.discriminatedUnion('chunk_type', [
  WebsocketChunkFullMessageSchema,
  WebsocketChunkAssistantMessageMarkerSchema,
  WebsocketChunkAssistantMessageFragmentSchema,
]);

// --- User Input ---

export const UserInputMessageSchema = z.object({
  content: z.string(),
});

// --- Exported Types ---

export type Message = z.infer<typeof MessageSchema>;
export type AssistantMessage = z.infer<typeof AssistantMessageSchema>;
export type NonStreamableMessage = z.infer<typeof NonStreamableMessageSchema>;
export type ToolCall = z.infer<typeof ToolCallSchema>;
export type WebsocketChunk = z.infer<typeof WebsocketChunkSchema>;
export type StreamingMessageMarkerPartStart = z.infer<typeof StreamingMessageMarkerPartStartSchema>;
export type UserInputMessage = z.infer<typeof UserInputMessageSchema>;

export interface StreamingAssistantMessage {
  role: 'assistant';
  content: string;
  reasoning: string;
  tool_calls: ToolCall[];
  errors: string[];
  metadata: {
    seq_in_session?: number | null;
  };
}

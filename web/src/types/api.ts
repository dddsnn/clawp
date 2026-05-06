// Copyright 2026 Marc Lehmann
//
// This file is part of clawp.
//
// clawp is free software: you can redistribute it and/or modify it under the
// terms of the GNU Affero General Public License as published by the Free
// Software Foundation, either version 3 of the License, or (at your option) any
// later version.
//
// clawp is distributed in the hope that it will be useful, but WITHOUT ANY
// WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
// A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
// details.
//
// You should have received a copy of the GNU Affero General Public License along
// with clawp. If not, see <https://www.gnu.org/licenses/>.

import { z } from 'zod';

export const Iso8601Schema = z.string().transform((str) => new Date(str));

export const StartMessageMetadataSchema = z.object({
  seq_in_session: z.number().nullable().optional(),
});

export const BaseChannelDescriptorSchema = z.object({
  type: z.enum(['malformed', 'missing', 'system', 'unknown', 'web_ui']),
});

export const MalformedChannelDescriptorSchema = BaseChannelDescriptorSchema.extend({
  type: z.literal('malformed'),
  error_message: z.string(),
});

export const SystemChannelDescriptorSchema = BaseChannelDescriptorSchema.extend({
  type: z.literal('system'),
});

export const UnknownChannelDescriptorSchema = BaseChannelDescriptorSchema.extend({
  type: z.literal('unknown'),
});

export const WebUiChannelDescriptorSchema = BaseChannelDescriptorSchema.extend({
  type: z.literal('web_ui'),
});

export type ChannelDescriptor =
  | z.infer<typeof MalformedChannelDescriptorSchema>
  | z.infer<typeof SystemChannelDescriptorSchema>
  | z.infer<typeof UnknownChannelDescriptorSchema>
  | z.infer<typeof WebUiChannelDescriptorSchema>
  | { type: 'missing'; fallback_channel: ChannelDescriptor };

// Using z.lazy because MissingChannelDescriptor references the union
export const MissingChannelDescriptorSchema: z.ZodType<Extract<ChannelDescriptor, { type: 'missing' }>> = BaseChannelDescriptorSchema.extend({
  type: z.literal('missing'),
  fallback_channel: z.lazy(() => ChannelDescriptorSchema),
});

export const ChannelDescriptorSchema: z.ZodType<ChannelDescriptor> = z.lazy(() => z.union([
  MalformedChannelDescriptorSchema,
  MissingChannelDescriptorSchema,
  SystemChannelDescriptorSchema,
  UnknownChannelDescriptorSchema,
  WebUiChannelDescriptorSchema,
]));

export const EndMessageMetadataSchema = z.object({
  time: Iso8601Schema,
  channel: ChannelDescriptorSchema,
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
  role: z.literal('agent'),
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
  chunk_type: z.enum(['full_message', 'agent_message_marker', 'agent_message_fragment']),
});

export const WebsocketChunkFullMessageSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('full_message'),
  payload: NonStreamableMessageSchema,
});

export const WebsocketChunkAssistantMessageMarkerSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('agent_message_marker'),
  payload: StreamingMessageMarkerSchema,
});

export const WebsocketChunkAssistantMessageFragmentSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('agent_message_fragment'),
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
  role: 'agent';
  content: string;
  reasoning: string;
  tool_calls: ToolCall[];
  errors: string[];
  metadata: {
    seq_in_session?: number | null;
  };
}

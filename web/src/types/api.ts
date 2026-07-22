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

export const BaseChatDescriptorSchema = z.object({
  channel: z.enum(['agent', 'matrix', 'web_ui']),
  chat_id: z.string(),
});

export const AgentChatDescriptorSchema = BaseChatDescriptorSchema.extend({
  channel: z.literal('agent'),
  chat_id: z.string().uuid(),
});

export const GithubChatDescriptorSchema = BaseChatDescriptorSchema.extend({
  channel: z.literal('github'),
  repo_full_name: z.string(),
  repo_clone_url: z.string(),
  issue_type: z.literal('issue', 'pr'),
  issue_number: z.int(),
  issue_title: z.string(),
  issue_author: z.string(),
});

export const MatrixChatDescriptorSchema = BaseChatDescriptorSchema.extend({
  channel: z.literal('matrix'),
  room_name: z.string().nullable(),
});

export const WebUiChatDescriptorSchema = BaseChatDescriptorSchema.extend({
  channel: z.literal('web_ui'),
  chat_id: z.literal(''),
});

export type ChatDescriptor =
| z.infer<typeof AgentChatDescriptorSchema>
| z.infer<typeof GithubChatDescriptorSchema>
| z.infer<typeof MatrixChatDescriptorSchema>
| z.infer<typeof WebUiChatDescriptorSchema>;


export const ChatDescriptorSchema: z.ZodType<ChatDescriptor> = z.lazy(() => z.union([
  AgentChatDescriptorSchema,
  MatrixChatDescriptorSchema,
  WebUiChatDescriptorSchema,
]));

export const BasicStartMessageMetadataSchema = z.object({
  chat: ChatDescriptorSchema,
});
export type BasicStartMessageMetadata = z.infer<typeof BasicStartMessageMetadataSchema>;

export const GithubStartMessageMetadataSchema = z.object({
  chat: GithubChatDescriptorSchema,
  comment_type: z.enum(['description', 'comment']),
});
export type GithubStartMessageMetadata = z.infer<typeof GithubStartMessageMetadataSchema>;

export const MatrixStartMessageMetadataSchema = z.object({
  chat: MatrixChatDescriptorSchema,
  sender_id: z.string(),
  sender_name: z.string().nullable(),
});
export type MatrixStartMessageMetadata = z.infer<typeof MatrixStartMessageMetadataSchema>;

export const StartMessageMetadataSchema: z.ZodType<StartMessageMetadata> = z.lazy(() => z.union([
  MatrixStartMessageMetadataSchema,
  GithubStartMessageMetadataSchema,
  BasicStartMessageMetadataSchema,
]));
export type StartMessageMetadata =
  | BasicStartMessageMetadata
  | GithubStartMessageMetadata
  | MatrixStartMessageMetadata;

export const EndMessageMetadataSchema = z.object({
  time: Iso8601Schema,
});

export const InternalMessageMetadataSchema = EndMessageMetadataSchema;
export type InternalMessageMetadata = z.infer<typeof InternalMessageMetadataSchema>;

export const BasicChatMessageMetadataSchema = BasicStartMessageMetadataSchema.merge(EndMessageMetadataSchema);
export type BasicChatMessageMetadata = z.infer<typeof BasicChatMessageMetadataSchema>;

export const GithubChatMessageMetadataSchema = GithubStartMessageMetadataSchema.merge(EndMessageMetadataSchema);
export type GithubChatMessageMetadata = z.infer<typeof GithubChatMessageMetadataSchema>;

export const MatrixChatMessageMetadataSchema = MatrixStartMessageMetadataSchema.merge(EndMessageMetadataSchema);
export type MatrixChatMessageMetadata = z.infer<typeof MatrixChatMessageMetadataSchema>;

export const ChatMessageMetadataSchema: z.ZodType<ChatMessageMetadata> = z.lazy(() => z.union([
  BasicChatMessageMetadataSchema,
  GithubChatMessageMetadataSchema,
  MatrixChatMessageMetadataSchema,
]));
export type ChatMessageMetadata = BasicChatMessageMetadata | MatrixChatMessageMetadata;

export const MessageMetadataSchema: z.ZodType<MessageMetadata> = z.lazy(() => z.union([
  InternalMessageMetadataSchema,
  ChatMessageMetadataSchema,
]));
export type MessageMetadata = InternalMessageMetadata | ChatMessageMetadata;

const BaseMessageSchema = z.object({
  metadata: MessageMetadataSchema,
  content: z.string(),
});

export const DeveloperMessageSchema = BaseMessageSchema.extend({
  role: z.literal('developer'),
  metadata: InternalMessageMetadataSchema,
});

export const SystemMessageSchema = BaseMessageSchema.extend({
  role: z.literal('system'),
  metadata: InternalMessageMetadataSchema,
});

export const ToolMessageSchema = BaseMessageSchema.extend({
  role: z.literal('tool'),
  metadata: InternalMessageMetadataSchema,
  tool_call_id: z.string(),
});

export const UserMessageSchema = BaseMessageSchema.extend({
  role: z.literal('user'),
  metadata: ChatMessageMetadataSchema,
});

export const ToolCallFunctionSchema = z.object({
  name: z.string().default(''),
  arguments: z.string().default(''),
});

export const ToolCallSchema = z.object({
  id: z.string(),
  function: ToolCallFunctionSchema,
});

export const AssistantMessageErrorSchema = z.object({
  type: z.string(),
  message: z.string(),
  kwargs: z.object(),
});

export const AssistantMessageSchema = BaseMessageSchema.extend({
  role: z.literal('agent'),
  metadata: ChatMessageMetadataSchema,
  reasoning: z.string(),
  tool_calls: z.array(ToolCallSchema),
  errors: z.array(AssistantMessageErrorSchema),
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

export const MessageOffsetSchema = z.object({
  session_seq: z.int(),
  message_seq: z.int(),
});

export const MessageInSessionSchema = z.object({
  message: MessageSchema,
  message_offset: MessageOffsetSchema,
});

// --- Streaming Markers ---

const BaseStreamingMessageMarkerSchema = z.object({
  marker_type: z.enum(['message_start', 'message_end', 'part_start', 'part_end']),
});

export const StreamingMessageMarkerMessageStartSchema = BaseStreamingMessageMarkerSchema.extend({
  marker_type: z.literal('message_start'),
  metadata: StartMessageMetadataSchema,
  message_offset: MessageOffsetSchema,
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

export const StreamingMessageFragmentErrorSchema = BaseStreamingMessageFragmentSchema.extend({
  fragment_type: z.literal('error'),
  fragment: AssistantMessageErrorSchema,
});

export const StreamingMessageFragmentSchema = z.discriminatedUnion('fragment_type', [
  StreamingMessageFragmentTextSchema,
  StreamingMessageFragmentToolCallSchema,
  StreamingMessageFragmentErrorSchema,
]);

// --- Websocket Chunks ---

const BaseWebsocketChunkSchema = z.object({
  chunk_type: z.enum(['full_message', 'agent_message_marker', 'agent_message_fragment']),
});

export const WebsocketChunkFullMessageSchema = BaseWebsocketChunkSchema.extend({
  chunk_type: z.literal('full_message'),
  payload: MessageInSessionSchema,
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

// --- Agent Types ---

export const AgentInformationSchema = z.object({
  id: z.string().uuid(),
});

export type AgentInformation = z.infer<typeof AgentInformationSchema>;

export const AgentPersonalityFileSchema = z.object({
  path: z.string(),
  description: z.string(),
});

export const AgentPersonalitySchema = z.object({
  name: z.string(),
  personality_files: z.array(AgentPersonalityFileSchema),
});

export const AgentPersonalityWithFileContentsSchema = AgentPersonalitySchema.extend({
  personality_file_contents: z.record(z.string(), z.string().nullable()),
});

export type AgentPersonality = z.infer<typeof AgentPersonalitySchema>;
export type AgentPersonalityWithFileContents = z.infer<typeof AgentPersonalityWithFileContentsSchema>;

export const GithubAccountConfigSchema = z.object({
  type: z.literal('github').default('github'),
  app_id: z.int(),
  installation_id: z.int(),
  organization: z.string(),
  id: z.string(),
});

export const MatrixAccountConfigSchema = z.object({
  type: z.literal('matrix').default('matrix'),
  homeserver: z.string(),
  username: z.string(),
  device_id: z.string(),
  id: z.string(),
});

export const BaseChannelStatusSchema = z.object({
  type: z.literal('github').default('github'),
  available: z.boolean(),
});

export const GithubChannelStatusSchema = BaseChannelStatusSchema.extend({
  type: z.literal('github').default('github'),
  app_id: z.int(),
  installation_id: z.int(),
  login: z.string(),
});

export const MatrixChannelStatusSchema = BaseChannelStatusSchema.extend({
  type: z.literal('matrix').default('matrix'),
});

export const ChannelStatusSchema = z.discriminatedUnion('type', [
  GithubChannelStatusSchema,
  MatrixChannelStatusSchema,
]);

export const ChannelConfigSchema = z.discriminatedUnion('type', [
  GithubAccountConfigSchema,
  MatrixAccountConfigSchema,
]);

export const ChannelInformationSchema = z.object({
  type: z.enum(['github', 'matrix']),
  id: z.string().nullable(),
  config: ChannelConfigSchema,
  status: ChannelStatusSchema,
  assigned_to_agent: z.uuid().nullable(),
});

export type ChannelInformation = z.infer<typeof ChannelInformationSchema>;

// --- Exported Types ---

export type Message = z.infer<typeof MessageSchema>;
export type MessageInSession = z.infer<typeof MessageInSessionSchema>;
export type MessageOffset = z.infer<typeof MessageOffsetSchema>;
export type AssistantMessageError = z.infer<typeof AssistantMessageErrorSchema>;
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
  errors: AssistantMessageError[];
  metadata: {
    chat: ChatDescriptor;
  };
}

export interface StreamingAssistantMessageInSession {
  message: StreamingAssistantMessage;
  message_offset: MessageOffset;
}

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

import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type {
  AssistantMessageError,
  MessageInSession,
  MessageOffset,
  ToolCall,
  StreamingMessageMarkerPartStart,
  StreamingAssistantMessageInSession,
  StartMessageMetadata,
} from "../types/api";

type ActivePartType = StreamingMessageMarkerPartStart['part_type'];

function sameMessageOffset(a: MessageOffset, b: MessageOffset): boolean {
  return a.session_seq === b.session_seq && a.message_seq === b.message_seq;
}

function compareMessageOffsets(a: MessageOffset, b: MessageOffset): number {
  return a.session_seq - b.session_seq || a.message_seq - b.message_seq;
}

export type MessageVisibilityMode = 'show' | 'hint' | 'hide';
export type ReasoningVisibilityMode = 'hide' | 'collapsed' | 'expanded';

type MessageVisibilityKey = 'systemDeveloper' | 'tool' | 'crossChannelConversation';

export type ConnectionState =
  | { status: 'uninitialized' }
  | { status: 'connected' }
  | { status: 'disconnected' }
  | { status: 'connecting', attempt: number, error?: string };

export type HistoryState =
  | { status: 'loading' }
  | { status: 'success' }
  | { status: 'error', error: string };

export const useChatStore = defineStore('chat', () => {
  const messages = ref<MessageInSession[]>([]);
  const visibility = ref({
    systemDeveloper: 'show' as MessageVisibilityMode,
    tool: 'show' as MessageVisibilityMode,
    crossChannelConversation: 'show' as MessageVisibilityMode,
    reasoning: 'collapsed' as ReasoningVisibilityMode,
  });

  const connectionState = ref<ConnectionState>({ status: 'uninitialized' });
  const historyState = ref<HistoryState>({ status: 'loading' });

  // Streaming State
  const activeStreamingMessage = ref<StreamingAssistantMessageInSession | null>(null);
  const activeStreamPartType = ref<ActivePartType | null>(null);

  const displayedMessages = computed(() => {
    const list = [...messages.value];
    if (activeStreamingMessage.value) {
      list.push(activeStreamingMessage.value as unknown as MessageInSession);
    }
    return list.sort((a, b) => compareMessageOffsets(a.message_offset, b.message_offset));
  });

  function setConnectionState(state: ConnectionState) {
    connectionState.value = state;
  }

  function setHistoryState(state: HistoryState) {
    historyState.value = state;
  }

  function addMessage(message: MessageInSession) {
    const existingMessage = messages.value.find((existing) =>
      sameMessageOffset(existing.message_offset, message.message_offset),
    );
    const matchesActiveStream = activeStreamingMessage.value && sameMessageOffset(
      activeStreamingMessage.value.message_offset,
      message.message_offset,
    );
    if (existingMessage || matchesActiveStream) {
      // The same message may arrive through history and the WebSocket in either order.
      return;
    }

    messages.value.push(message);
    messages.value.sort((a, b) => compareMessageOffsets(a.message_offset, b.message_offset));
  }

  function clearMessages() {
    messages.value = [];
    activeStreamingMessage.value = null;
    activeStreamPartType.value = null;
    historyState.value = { status: 'loading' };
  }

  // Used by the stream to create the placeholder agent message before fragments arrive
  function startStreamingMessage(messageOffset: MessageOffset, metadata: StartMessageMetadata) {
    const existingMsg = messages.value.find((message) =>
      sameMessageOffset(message.message_offset, messageOffset),
    );
    if (existingMsg) {
      // If we already have this message (e.g. from history), we should not start a new stream for it.
      activeStreamingMessage.value = null;
      return;
    }

    activeStreamingMessage.value = {
      message: {
        role: 'agent',
        content: '',
        reasoning: '',
        tool_calls: [],
        errors: [],
        metadata: metadata,
      },
      message_offset: messageOffset,
    };
  }

  function endStreamingMessage(time: Date) {
    if (!activeStreamingMessage.value) return;

    const finalizedMessage: MessageInSession = {
      message: {
        ...activeStreamingMessage.value.message,
        metadata: {
          ...activeStreamingMessage.value.message.metadata,
          time,
        },
      },
      message_offset: activeStreamingMessage.value.message_offset,
    };

    activeStreamingMessage.value = null;
    activeStreamPartType.value = null;
    addMessage(finalizedMessage);
  }

  function setActivePartType(type: ActivePartType) {
    activeStreamPartType.value = type;
  }

  function clearActivePartType() {
    activeStreamPartType.value = null;
  }

  function appendStreamFragmentText(text: string) {
    if (!activeStreamingMessage.value || !activeStreamPartType.value) return;

    if (activeStreamPartType.value === 'content') {
      activeStreamingMessage.value.message.content += text;
    } else if (activeStreamPartType.value === 'reasoning') {
      activeStreamingMessage.value.message.reasoning += text;
    }
  }

  function appendStreamFragmentToolCall(toolCall: ToolCall) {
    if (!activeStreamingMessage.value || activeStreamPartType.value !== 'tool') return;
    activeStreamingMessage.value.message.tool_calls.push(toolCall);
  }

  function appendStreamFragmentError(error: AssistantMessageError) {
    if (!activeStreamingMessage.value || activeStreamPartType.value !== 'error') return;
    activeStreamingMessage.value.message.errors.push(error);
  }

  function cycleMessageVisibility(key: MessageVisibilityKey) {
    const modes: MessageVisibilityMode[] = ['show', 'hint', 'hide'];
    const currentMode = visibility.value[key];
    const currentIndex = modes.indexOf(currentMode);
    const nextIndex = (currentIndex + 1) % modes.length;
    visibility.value[key] = modes[nextIndex];
  }

  function cycleReasoningVisibility() {
    const modes: ReasoningVisibilityMode[] = ['hide', 'collapsed', 'expanded'];
    const currentMode = visibility.value.reasoning;
    const currentIndex = modes.indexOf(currentMode);
    const nextIndex = (currentIndex + 1) % modes.length;
    visibility.value.reasoning = modes[nextIndex];
  }

  return {
    messages,
    displayedMessages,
    visibility,
    connectionState,
    historyState,
    activeStreamingMessage,
    activeStreamPartType,
    setConnectionState,
    setHistoryState,
    addMessage,
    clearMessages,
    startStreamingMessage,
    endStreamingMessage,
    setActivePartType,
    clearActivePartType,
    appendStreamFragmentText,
    appendStreamFragmentToolCall,
    appendStreamFragmentError,
    cycleMessageVisibility,
    cycleReasoningVisibility,
  };
});

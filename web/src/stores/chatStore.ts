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
import type { Message, AssistantMessage, ToolCall, StreamingMessageMarkerPartStart, StreamingAssistantMessage } from '../types/api';

type ActivePartType = StreamingMessageMarkerPartStart['part_type'];

export type ConnectionStatus = 'connecting' | 'connected' | 'error' | 'disconnected';

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([]);
  const visibility = ref({
    system: true,
    tool: true,
    developer: true,
  });

  const connectionStatus = ref<ConnectionStatus>('connecting');

  // Streaming State
  const activeStreamingMessage = ref<StreamingAssistantMessage | null>(null);
  const activeStreamPartType = ref<ActivePartType | null>(null);

  const displayedMessages = computed(() => {
    const list = [...messages.value];
    if (activeStreamingMessage.value) {
      list.push(activeStreamingMessage.value as unknown as Message);
    }
    return list;
  });

  function setConnectionStatus(status: ConnectionStatus) {
    connectionStatus.value = status;
  }

  function addMessage(message: Message) {
    const seq = message.metadata.seq_in_session;
    if (seq === undefined || seq === null) {
      // If no sequence number, just push it (e.g. local optimistic message before server ack)
      messages.value.push(message);
      return;
    }

    // Check if we already have a message with this sequence number
    const existingIndex = messages.value.findIndex(m => m.metadata.seq_in_session === seq);
    if (existingIndex !== -1) {
      // If it exists, we assume it's already being handled (e.g. streaming or already complete from history).
      return;
    }

    // Insert maintaining order
    messages.value.push(message);
    messages.value.sort((a, b) => (a.metadata.seq_in_session ?? 0) - (b.metadata.seq_in_session ?? 0));
  }

  // Used by the stream to create the placeholder assistant message before fragments arrive
  function startStreamingMessage(seqInSession: number | null | undefined) {
    if (seqInSession !== undefined && seqInSession !== null) {
      const existingMsg = messages.value.find(m => m.metadata.seq_in_session === seqInSession);
      if (existingMsg) {
        // If we already have this message (e.g. from history), we should not start a new stream for it.
        activeStreamingMessage.value = null;
        return;
      }
    }

    activeStreamingMessage.value = {
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_calls: [],
      errors: [],
      metadata: {
        seq_in_session: seqInSession,
      },
    };
  }

  function endStreamingMessage(time: Date) {
    if (!activeStreamingMessage.value) return;
    
    const finalizedMessage: AssistantMessage = {
      ...activeStreamingMessage.value,
      metadata: {
        ...activeStreamingMessage.value.metadata,
        time,
      },
    };
    
    addMessage(finalizedMessage);
    activeStreamingMessage.value = null;
    activeStreamPartType.value = null;
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
      activeStreamingMessage.value.content += text;
    } else if (activeStreamPartType.value === 'reasoning') {
      activeStreamingMessage.value.reasoning += text;
    } else if (activeStreamPartType.value === 'error') {
      activeStreamingMessage.value.errors.push(text);
    }
  }

  function appendStreamFragmentToolCall(toolCall: ToolCall) {
    if (!activeStreamingMessage.value || activeStreamPartType.value !== 'tool') return;
    
    activeStreamingMessage.value.tool_calls.push(toolCall);
  }

  function toggleVisibility(role: 'system' | 'tool' | 'developer') {
    visibility.value[role] = !visibility.value[role];
  }

  return {
    messages,
    displayedMessages,
    visibility,
    connectionStatus,
    activeStreamingMessage,
    activeStreamPartType,
    setConnectionStatus,
    addMessage,
    startStreamingMessage,
    endStreamingMessage,
    setActivePartType,
    clearActivePartType,
    appendStreamFragmentText,
    appendStreamFragmentToolCall,
    toggleVisibility,
  };
});

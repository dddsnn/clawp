import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { Message, AssistantMessage, ToolCall, StreamingMessageMarkerPartStart } from '../types/api';

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
  const activeStreamMessageId = ref<string | null>(null);
  const activeStreamPartType = ref<ActivePartType | null>(null);

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
        // We will return early and not set activeStreamMessageId, so subsequent fragments will be ignored.
        activeStreamMessageId.value = null;
        return;
      }
    }

    // Generate a temporary unique ID for tracking the stream locally until the final ID is known
    const tempId = crypto.randomUUID();
    const newMsg: AssistantMessage = {
      role: 'assistant',
      content: '',
      reasoning: '',
      tool_calls: [],
      errors: [],
      metadata: {
        seq_in_session: seqInSession,
        time: new Date().toISOString(), // Temporary until message_end provides final time
      },
    };
    // Let's attach our local tempId so we can find it
    (newMsg as any)._localId = tempId;
    addMessage(newMsg);
    activeStreamMessageId.value = tempId;
  }

  function endStreamingMessage(time: string) {
    if (!activeStreamMessageId.value) return;
    const msg = messages.value.find((m) => (m as any)._localId === activeStreamMessageId.value);
    if (msg) {
      msg.metadata.time = time;
    }
    activeStreamMessageId.value = null;
    activeStreamPartType.value = null;
  }

  function setActivePartType(type: ActivePartType) {
    activeStreamPartType.value = type;
  }

  function clearActivePartType() {
    activeStreamPartType.value = null;
  }

  function appendStreamFragmentText(text: string) {
    if (!activeStreamMessageId.value || !activeStreamPartType.value) return;
    
    const msg = messages.value.find((m) => (m as any)._localId === activeStreamMessageId.value) as AssistantMessage | undefined;
    if (!msg) return;

    if (activeStreamPartType.value === 'content') {
      msg.content += text;
    } else if (activeStreamPartType.value === 'reasoning') {
      msg.reasoning += text;
    } else if (activeStreamPartType.value === 'error') {
      msg.errors.push(text);
    }
  }

  function appendStreamFragmentToolCall(toolCall: ToolCall) {
    if (!activeStreamMessageId.value || activeStreamPartType.value !== 'tool') return;
    
    const msg = messages.value.find((m) => (m as any)._localId === activeStreamMessageId.value) as AssistantMessage | undefined;
    if (!msg) return;

    msg.tool_calls.push(toolCall);
  }

  function toggleVisibility(role: 'system' | 'tool' | 'developer') {
    visibility.value[role] = !visibility.value[role];
  }

  return {
    messages,
    visibility,
    connectionStatus,
    activeStreamMessageId,
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

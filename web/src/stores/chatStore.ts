import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { Message, AssistantMessage, ToolCall, StreamingMessageMarkerPartStart } from '../types/api';

type ActivePartType = StreamingMessageMarkerPartStart['part_type'];

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([]);
  const visibility = ref({
    system: true,
    tool: true,
    developer: true,
  });

  // Streaming State
  const activeStreamMessageId = ref<string | null>(null);
  const activeStreamPartType = ref<ActivePartType | null>(null);

  function setMessages(newMessages: Message[]) {
    messages.value = newMessages;
  }

  function addMessage(message: Message) {
    messages.value.push(message);
  }

  // Used by the stream to create the placeholder assistant message before fragments arrive
  function startStreamingMessage(seqInSession: number | null | undefined) {
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
    messages.value.push(newMsg);
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
      // In the stream protocol, errors might come as multiple text fragments for a single error.
      // Or they might be discrete strings. Let's just append to the last string if it exists, or push a new one.
      if (msg.errors.length === 0) {
        msg.errors.push(text);
      } else {
        msg.errors[msg.errors.length - 1] += text;
      }
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
    activeStreamMessageId,
    activeStreamPartType,
    setMessages,
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

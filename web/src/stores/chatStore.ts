import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { Message } from '../types/chat';

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([]);
  const visibility = ref({
    system: true,
    tool: true,
    developer: true,
  });

  function addMessage(msg: Omit<Message, 'id'>) {
    const message: Message = {
      ...msg,
      id: crypto.randomUUID(),
    };
    messages.value.push(message);
    return message;
  }

  function appendStreamFragment(id: string, text: string, type: 'content' | 'reasoning' = 'content') {
    const msg = messages.value.find((m) => m.id === id);
    if (msg) {
      if (type === 'content') {
        msg.content += text;
      } else if (type === 'reasoning') {
        msg.reasoning = (msg.reasoning || '') + text;
      }
    }
  }

  function toggleVisibility(role: 'system' | 'tool' | 'developer') {
    visibility.value[role] = !visibility.value[role];
  }

  return {
    messages,
    visibility,
    addMessage,
    appendStreamFragment,
    toggleVisibility,
  };
});

import { useChatStore } from '../stores/chatStore';

export class MockAgentService {
  private store: ReturnType<typeof useChatStore>;

  constructor() {
    this.store = useChatStore();
  }

  async sendMessage(userMessage: string) {
    // 1. Add user message
    this.store.addMessage({
      role: 'user',
      content: userMessage,
    });

    // 2. Simulate network delay
    await new Promise((resolve) => setTimeout(resolve, 500));

    // 3. Create agent message
    const agentMsg = this.store.addMessage({
      role: 'agent',
      content: '',
      reasoning: '',
    });

    // 4. Simulate streaming reasoning
    const reasoningChunks = ['I need ', 'to think ', 'about this ', 'carefully. ', 'Ah, ', 'I know ', 'the answer.'];
    for (const chunk of reasoningChunks) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      this.store.appendStreamFragment(agentMsg.id, chunk, 'reasoning');
    }

    // 5. Simulate streaming content
    const contentChunks = ['Here ', 'is ', 'your ', 'response ', 'to: ', `"${userMessage}". `, 'I hope ', 'this helps!'];
    for (const chunk of contentChunks) {
      await new Promise((resolve) => setTimeout(resolve, 150));
      this.store.appendStreamFragment(agentMsg.id, chunk, 'content');
    }
  }
}

import { useChatStore } from '../stores/chatStore';
import { z } from 'zod';
import { MessageSchema, WebsocketChunkSchema } from '../types/api';
import type { WebsocketChunk } from '../types/api';

const MessagesResponseSchema = z.array(MessageSchema);

export class ApiService {
  private store: ReturnType<typeof useChatStore>;
  private ws: WebSocket | null = null;
  private wsBuffer: WebsocketChunk[] = [];
  private highestHistorySeq: number = -1;
  private historyLoaded = false;
  private ignoreCurrentStream = false;

  constructor() {
    this.store = useChatStore();
  }

  async init() {
    this.connectWebSocket();
    await this.fetchHistory();
  }

  private connectWebSocket() {
    // Vite proxy handles /api to the backend
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/stream`;
    
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
    };

    this.ws.onmessage = (event) => {
      try {
        const rawData = JSON.parse(event.data);
        const chunk = WebsocketChunkSchema.parse(rawData);
        
        if (!this.historyLoaded) {
          this.wsBuffer.push(chunk);
        } else {
          this.processChunk(chunk);
        }
      } catch (error) {
        console.error('Failed to parse or validate websocket message:', error, event.data);
      }
    };

    this.ws.onclose = () => {
      console.log('WebSocket disconnected. Reconnecting in 3s...');
      setTimeout(() => this.connectWebSocket(), 3000);
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  private async fetchHistory() {
    try {
      const response = await fetch('/api/v1/messages');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const rawData = await response.json();
      const messages = MessagesResponseSchema.parse(rawData);
      
      // Find highest seq_in_session
      this.highestHistorySeq = messages.reduce((max, msg) => {
        const seq = msg.metadata.seq_in_session;
        return typeof seq === 'number' && seq > max ? seq : max;
      }, -1);

      this.store.setMessages(messages);
      this.historyLoaded = true;

      // Process any chunks that arrived while we were fetching
      this.flushBuffer();

    } catch (error) {
      console.error('Failed to fetch or validate message history:', error);
      // Even if history fails, we should unblock the stream to try and get new messages
      this.historyLoaded = true;
      this.flushBuffer();
    }
  }

  private flushBuffer() {
    const chunks = [...this.wsBuffer];
    this.wsBuffer = [];
    for (const chunk of chunks) {
      this.processChunk(chunk);
    }
  }

  private processChunk(chunk: WebsocketChunk) {
    if (chunk.chunk_type === 'full_message') {
      const msg = chunk.payload;
      const seq = msg.metadata.seq_in_session;
      
      // Discard if we already have it from history
      if (typeof seq === 'number' && seq <= this.highestHistorySeq) {
        return;
      }
      this.store.addMessage(msg);
      return;
    }

    if (chunk.chunk_type === 'assistant_message_marker') {
      const marker = chunk.payload;
      
      if (marker.marker_type === 'message_start') {
        const seq = marker.metadata.seq_in_session;
        // If the stream is for a message we already have fully loaded via history,
        // we need to ignore all subsequent stream parts/fragments until message_end.
        if (typeof seq === 'number' && seq <= this.highestHistorySeq) {
          this.ignoreCurrentStream = true;
          return;
        }
        this.ignoreCurrentStream = false;
        this.store.startStreamingMessage(seq ?? undefined);
      } 
      else if (marker.marker_type === 'part_start') {
        if (!this.ignoreCurrentStream) {
          this.store.setActivePartType(marker.part_type);
        }
      } 
      else if (marker.marker_type === 'part_end') {
        if (!this.ignoreCurrentStream) {
          this.store.clearActivePartType();
        }
      } 
      else if (marker.marker_type === 'message_end') {
        if (!this.ignoreCurrentStream) {
          this.store.endStreamingMessage(marker.metadata.time);
        }
        this.ignoreCurrentStream = false;
      }
      return;
    }

    if (chunk.chunk_type === 'assistant_message_fragment' && !this.ignoreCurrentStream) {
      const frag = chunk.payload;
      if (frag.fragment_type === 'text') {
        this.store.appendStreamFragmentText(frag.fragment);
      } else if (frag.fragment_type === 'tool_call') {
        this.store.appendStreamFragmentToolCall(frag.fragment);
      }
    }
  }

  // Placeholder for when we add send functionality
  async sendMessage(text: string) {
    console.warn("Sending messages not yet implemented. Cannot send:", text);
  }
}

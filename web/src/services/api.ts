import { useChatStore } from '../stores/chatStore';
import { z } from 'zod';
import { MessageSchema, WebsocketChunkSchema } from '../types/api';
import type { WebsocketChunk } from '../types/api';

const MessagesResponseSchema = z.array(MessageSchema);

export class ApiService {
  private store: ReturnType<typeof useChatStore>;
  private ws: WebSocket | null = null;
  private wsConnectionPromise: Promise<void> | null = null;
  private isIntentionallyClosed = false;

  constructor() {
    this.store = useChatStore();
  }

  async init() {
    this.isIntentionallyClosed = false;
    this.store.setConnectionStatus('connecting');
    try {
      await this.connectWebSocket();
      await this.fetchHistory();
      this.store.setConnectionStatus('connected');
    } catch (e) {
      console.error("Failed to initialize API:", e);
      this.store.setConnectionStatus('error');
    }
  }

  disconnect() {
    this.isIntentionallyClosed = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.store.setConnectionStatus('disconnected');
  }

  private connectWebSocket(): Promise<void> {
    if (this.wsConnectionPromise) return this.wsConnectionPromise;

    this.wsConnectionPromise = new Promise((resolve, reject) => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/v1/stream`;
      
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        console.log('WebSocket connected');
        this.store.setConnectionStatus('connected');
        resolve();
      };

      this.ws.onmessage = (event) => {
        try {
          const rawData = JSON.parse(event.data);
          const chunk = WebsocketChunkSchema.parse(rawData);
          this.processChunk(chunk);
        } catch (error) {
          console.error('Failed to parse or validate websocket message:', error, event.data);
        }
      };

      this.ws.onclose = () => {
        if (this.isIntentionallyClosed) {
          console.log('WebSocket intentionally disconnected.');
          return;
        }
        console.log('WebSocket disconnected. Reconnecting in 3s...');
        this.store.setConnectionStatus('connecting');
        this.wsConnectionPromise = null;
        setTimeout(() => this.connectWebSocket(), 3000);
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        this.store.setConnectionStatus('error');
        reject(error);
      };
    });

    return this.wsConnectionPromise;
  }

  private async fetchHistory() {
    try {
      const response = await fetch('/api/v1/messages');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const rawData = await response.json();
      const messages = MessagesResponseSchema.parse(rawData);
      
      for (const msg of messages) {
        this.store.addMessage(msg);
      }

    } catch (error) {
      console.error('Failed to fetch or validate message history:', error);
      throw error;
    }
  }

  private processChunk(chunk: WebsocketChunk) {
    if (chunk.chunk_type === 'full_message') {
      this.store.addMessage(chunk.payload);
      return;
    }

    if (chunk.chunk_type === 'assistant_message_marker') {
      const marker = chunk.payload;
      
      if (marker.marker_type === 'message_start') {
        const seq = marker.metadata.seq_in_session;
        this.store.startStreamingMessage(seq ?? undefined);
      } 
      else if (marker.marker_type === 'part_start') {
        this.store.setActivePartType(marker.part_type);
      } 
      else if (marker.marker_type === 'part_end') {
        this.store.clearActivePartType();
      } 
      else if (marker.marker_type === 'message_end') {
        this.store.endStreamingMessage(marker.metadata.time);
      }
      return;
    }

    if (chunk.chunk_type === 'assistant_message_fragment') {
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

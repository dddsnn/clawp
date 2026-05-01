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

import { useChatStore } from '../stores/chatStore';
import { z } from 'zod';
import { MessageSchema, WebsocketChunkSchema } from '../types/api';
import type { WebsocketChunk, UserInputMessage } from '../types/api';

const MessagesResponseSchema = z.array(MessageSchema);

export class ApiService {
  private store: ReturnType<typeof useChatStore>;
  private ws: WebSocket | null = null;
  private isIntentionallyClosed = false;

  constructor() {
    this.store = useChatStore();
  }

  init() {
    this.isIntentionallyClosed = false;
    this.store.setConnectionStatus('connecting');
    this.startWebSocketConnection();
  }

  disconnect() {
    this.isIntentionallyClosed = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.store.setConnectionStatus('disconnected');
  }

  private startWebSocketConnection() {
    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/v1/stream`;

      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = async () => {
        console.log('WebSocket connected.');
        this.store.setConnectionStatus('connected');
        // Fetch the entire history every time we connect (even on a
        // reconnect) to ensure we've not missed anything.
        await this.fetchHistory();
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

        setTimeout(() => connect(), 3000);
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        this.store.setConnectionStatus('error');
        // We don't reject here; we let onclose handle the rejection for the initial connection
        // because onerror is always followed by onclose.
      };
    };

    connect();
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
        // The store's addMessage() is idempotent, it checks whether the
        // message exists already before adding it.
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

  async sendMessage(text: string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error("WebSocket is not connected. Cannot send message.");
      return;
    }
    const message: UserInputMessage = { content: text };
    this.ws.send(JSON.stringify(message));
  }
}

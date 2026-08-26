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
// You should have received a copy of the GNU Affero General Public License
// along with clawp. If not, see <https://www.gnu.org/licenses/>.

import { z } from 'zod';
import { useChatStore } from '../stores/chatStore';
import {
  MessageInSessionSchema,
  WebsocketChunkSchema,
  AgentInformationSchema,
  AgentPersonalitySchema,
  AgentPersonalityWithFileContentsSchema,
  ChannelInformationSchema,
  UserInputMessageSchema,
} from '../types/api';
import type {
  WebsocketChunk,
  UserInputMessage,
  AgentInformation,
  AgentPersonality,
  AgentPersonalityWithFileContents,
  ChannelInformation,
} from '../types/api';

const MessagesInSessionResponseSchema = z.array(MessageInSessionSchema);
const AgentsResponseSchema = z.array(AgentInformationSchema);
const PersonalitiesResponseSchema = z.array(AgentPersonalitySchema);
const ChannelsResponseSchema = z.array(ChannelInformationSchema);

export async function fetchAgents(): Promise<AgentInformation[]> {
  const response = await fetch('/api/v1/agents');
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const rawData = await response.json();
  return AgentsResponseSchema.parse(rawData);
}

export async function hatchAgent(agentName: string, personalityName: string): Promise<AgentInformation> {
  const params = new URLSearchParams({
    agent_name: agentName,
    personality_name: personalityName,
  });
  const response = await fetch(`/api/v1/agents/hatch?${params}`);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const rawData = await response.json();
  return AgentInformationSchema.parse(rawData);
}

export async function fetchHistory(agentId: string) {
  const response = await fetch(`/api/v1/agents/${agentId}/messages`);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const rawData = await response.json();
  return MessagesInSessionResponseSchema.parse(rawData);
}

export async function fetchPersonalities(): Promise<AgentPersonality[]> {
  const response = await fetch('/api/v1/personalities');
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const rawData = await response.json();
  return PersonalitiesResponseSchema.parse(rawData);
}

export async function fetchPersonality(personalityName: string): Promise<AgentPersonalityWithFileContents> {
  const response = await fetch(`/api/v1/personalities/${encodeURIComponent(personalityName)}`);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const rawData = await response.json();
  return AgentPersonalityWithFileContentsSchema.parse(rawData);
}

export async function fetchChannels(): Promise<ChannelInformation[]> {
  const response = await fetch('/api/v1/channels');
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  const rawData = await response.json();
  return ChannelsResponseSchema.parse(rawData);
}

export async function assignChannel(channelType: string, channelId: string, agentId: string): Promise<ChannelInformation> {
  const encodedChannelType = encodeURIComponent(channelType);
  const encodedChannelId = encodeURIComponent(channelId);
  const encodedAgentId = encodeURIComponent(agentId);
  const response = await fetch(`/api/v1/channels/${encodedChannelType}/${encodedChannelId}/assignment/${encodedAgentId}`, {
    method: 'POST',
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  const rawData = await response.json();
  return ChannelInformationSchema.parse(rawData);
}

export async function unassignChannel(channelType: string, channelId: string, agentId: string): Promise<void> {
  const encodedChannelType = encodeURIComponent(channelType);
  const encodedChannelId = encodeURIComponent(channelId);
  const encodedAgentId = encodeURIComponent(agentId);
  const response = await fetch(`/api/v1/channels/${encodedChannelType}/${encodedChannelId}/assignment/${encodedAgentId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
}

export class ChatConnection {
  private ws: WebSocket | null = null;
  private isDestroyed = false;
  private store: ReturnType<typeof useChatStore>;
  public readonly agentId: string;

  constructor(agentId: string) {
    this.agentId = agentId;
    this.store = useChatStore();
  }

  connect() {
    this.isDestroyed = false;
    this.store.clearMessages();
    this.store.setConnectionState({ status: 'connecting', attempt: 1 });
    this.connectWebSocket();
  }

  disconnect() {
    this.isDestroyed = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.store.setConnectionState({ status: 'disconnected' });
  }

  private connectWebSocket() {
    let attemptCounter = 1;

    const connectLoop = () => {
      if (this.isDestroyed) return;

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/v1/agents/${this.agentId}/stream/${Date.now()}`;

      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = async () => {
        if (this.isDestroyed) {
          this.ws?.close();
          return;
        }
        console.log(`WebSocket connected to agent ${this.agentId}.`);
        attemptCounter = 1;
        this.store.setConnectionState({ status: 'connected' });

        try {
          const messages = await fetchHistory(this.agentId);
          if (this.isDestroyed) return;
          for (const msg of messages) {
            this.store.addMessage(msg);
          }
          this.store.setHistoryState({ status: 'success' });
        } catch (error) {
          console.error('Failed to fetch history:', error);
          if (!this.isDestroyed) {
            this.store.setHistoryState({ status: 'error', error: error instanceof Error ? error.message : 'Unknown error' });
          }
        }
      };

      this.ws.onmessage = (event) => {
        if (this.isDestroyed) return;
        try {
          const rawData = JSON.parse(event.data);
          const chunk = WebsocketChunkSchema.parse(rawData);
          this.processChunk(chunk);
        } catch (error) {
          console.error('Failed to parse websocket message:', error, event.data);
        }
      };

      this.ws.onclose = () => {
        if (this.isDestroyed) return;

        console.log(`WebSocket disconnected. Reconnecting in 3s... (Attempt ${attemptCounter})`);

        const currentState = this.store.connectionState;
        const errorMessage = currentState.status === 'connecting' ? currentState.error : undefined;

        this.store.setConnectionState({
          status: 'connecting', 
          attempt: attemptCounter,
          error: errorMessage
        });

        attemptCounter++;
        setTimeout(() => connectLoop(), 3000);
      };

      this.ws.onerror = (error) => {
        if (this.isDestroyed) return;
        const currentState = this.store.connectionState;
        const isNormalReconnection = currentState.status === 'connecting' && !currentState.error;
        let errorMessage;
        if (!isNormalReconnection) {
          errorMessage = "Websocket connection error";
          console.error('WebSocket error:', error);
        }
        this.store.setConnectionState({
          status: 'connecting',
          attempt: attemptCounter,
          error: errorMessage
        });
      };
    };

    connectLoop();
  }

  private processChunk(chunk: WebsocketChunk) {
    if (chunk.chunk_type === 'full_message') {
      this.store.addMessage(chunk.payload);
      return;
    }

    if (chunk.chunk_type === 'agent_message_marker') {
      const marker = chunk.payload;
      
      if (marker.marker_type === 'message_start') {
        this.store.startStreamingMessage(marker.message_offset, marker.metadata);
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

    if (chunk.chunk_type === 'agent_message_fragment') {
      const frag = chunk.payload;
      if (frag.fragment_type === 'text') {
        this.store.appendStreamFragmentText(frag.fragment);
      } else if (frag.fragment_type === 'tool_call') {
        this.store.appendStreamFragmentToolCall(frag.fragment);
      } else if (frag.fragment_type === 'error') {
        this.store.appendStreamFragmentError(frag.fragment);
      }
    }
  }

  sendUserInput(input: UserInputMessage) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error("WebSocket is not connected. Cannot send message.");
      return;
    }
    const message = UserInputMessageSchema.parse(input);
    this.ws.send(JSON.stringify(message));
  }
}

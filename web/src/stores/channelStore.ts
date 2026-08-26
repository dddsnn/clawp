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

import { defineStore } from 'pinia';
import { ref } from 'vue';
import { fetchChannels as fetchChannelsApi } from '../services/api';
import type { ChannelInformation } from '../types/api';

export function getChannelKey(channel: Pick<ChannelInformation, 'type' | 'id'>): string {
  return `${channel.type}:${channel.id ?? ''}`;
}

export const useChannelStore = defineStore('channel', () => {
  const channels = ref<ChannelInformation[]>([]);
  const channelsLoading = ref(false);
  const channelsError = ref<string | null>(null);

  async function fetchChannels() {
    try {
      channelsLoading.value = true;
      channelsError.value = null;
      channels.value = await fetchChannelsApi();
    } catch (error) {
      console.error('Failed to load channels:', error);
      channelsError.value = error instanceof Error ? error.message : String(error);
      channels.value = [];
    } finally {
      channelsLoading.value = false;
    }
  }

  function upsertChannel(updatedChannel: ChannelInformation) {
    const key = getChannelKey(updatedChannel);
    const existingIndex = channels.value.findIndex((channel) => getChannelKey(channel) === key);

    if (existingIndex === -1) {
      channels.value = [...channels.value, updatedChannel];
      return;
    }

    channels.value = channels.value.map((channel) => {
      if (getChannelKey(channel) !== key) {
        return channel;
      }

      return updatedChannel;
    });
  }

  function setAssignedAgent(channelKey: string, agentId: string | null) {
    channels.value = channels.value.map((channel) => {
      if (getChannelKey(channel) !== channelKey) {
        return channel;
      }

      return {
        ...channel,
        assigned_to_agent: agentId,
      };
    });
  }

  return {
    channels,
    channelsLoading,
    channelsError,
    fetchChannels,
    upsertChannel,
    setAssignedAgent,
  };
});

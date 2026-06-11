<!--
Copyright 2026 Marc Lehmann

This file is part of clawp.

clawp is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.

clawp is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
details.

You should have received a copy of the GNU Affero General Public License along
with clawp. If not, see <https://www.gnu.org/licenses/>.
-->

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import ChannelDetails from './ChannelDetails.vue';
import { useChannelStore, getChannelKey } from '../../stores/channelStore';
import { useAgentStore } from '../../stores/agentStore';
import { assignChannel, unassignChannel } from '../../services/api';

const props = defineProps<{
  channelKey: string;
}>();

const channelStore = useChannelStore();
const agentStore = useAgentStore();
const { channels } = storeToRefs(channelStore);
const { agents } = storeToRefs(agentStore);

const selectedAgentId = ref('');
const isAssigning = ref(false);
const isUnassigning = ref(false);
const assignmentError = ref<string | null>(null);

const channel = computed(() => {
  return channels.value.find((candidate) => getChannelKey(candidate) === props.channelKey) ?? null;
});

watch(
  [channel, agents],
  ([selectedChannel, availableAgents]) => {
    assignmentError.value = null;

    if (selectedChannel?.assigned_to_agent) {
      selectedAgentId.value = selectedChannel.assigned_to_agent;
      return;
    }

    if (selectedAgentId.value && availableAgents.some((agent) => agent.id === selectedAgentId.value)) {
      return;
    }

    selectedAgentId.value = availableAgents[0]?.id ?? '';
  },
  { immediate: true },
);

const handleAssign = async () => {
  if (!channel.value || channel.value.id === null || !selectedAgentId.value || isAssigning.value) {
    return;
  }

  assignmentError.value = null;
  isAssigning.value = true;

  try {
    const updatedChannel = await assignChannel(channel.value.type, channel.value.id, selectedAgentId.value);
    channelStore.upsertChannel(updatedChannel);
  } catch (error) {
    console.error('Failed to assign channel:', error);
    assignmentError.value = error instanceof Error ? error.message : String(error);
  } finally {
    isAssigning.value = false;
  }
};

const handleUnassign = async () => {
  if (!channel.value || channel.value.id === null || !channel.value.assigned_to_agent || isUnassigning.value) {
    return;
  }

  assignmentError.value = null;
  isUnassigning.value = true;

  try {
    await unassignChannel(channel.value.type, channel.value.id, channel.value.assigned_to_agent);
    channelStore.setAssignedAgent(getChannelKey(channel.value), null);
  } catch (error) {
    console.error('Failed to unassign channel:', error);
    assignmentError.value = error instanceof Error ? error.message : String(error);
  } finally {
    isUnassigning.value = false;
  }
};

const handleUpdateSelectedAgent = (newSelectedAgentId: string) => {
  selectedAgentId.value = newSelectedAgentId;
};
</script>

<template>
  <ChannelDetails
    :channel="channel"
    :agents="agents"
    :selected-agent-id="selectedAgentId"
    :is-assigning="isAssigning"
    :is-unassigning="isUnassigning"
    :assignment-error="assignmentError"
    @assign="handleAssign"
    @unassign="handleUnassign"
    @update:selected-agent-id="handleUpdateSelectedAgent"
  />
</template>

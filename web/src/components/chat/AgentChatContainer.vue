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
import { onUnmounted, shallowRef, watch } from 'vue';
import ChatWindow from './ChatWindow.vue';
import { ChatConnection } from '../../services/api';
import type { UserInputMessage } from '../../types/api';

const props = defineProps<{
  agentId: string;
}>();

const currentConnection = shallowRef<ChatConnection | null>(null);

const disconnectCurrentConnection = () => {
  if (!currentConnection.value) {
    return;
  }

  currentConnection.value.disconnect();
  currentConnection.value = null;
};

const handleSend = (message: UserInputMessage) => {
  currentConnection.value?.sendUserInput(message);
};

watch(
  () => props.agentId,
  (newAgentId) => {
    disconnectCurrentConnection();

    currentConnection.value = new ChatConnection(newAgentId);
    currentConnection.value.connect();
  },
  { immediate: true },
);

onUnmounted(() => {
  disconnectCurrentConnection();
});
</script>

<template>
  <ChatWindow @send="handleSend" />
</template>

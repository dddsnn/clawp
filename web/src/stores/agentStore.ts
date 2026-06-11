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

import { defineStore } from 'pinia';
import { ref } from 'vue';
import { fetchAgents as fetchAgentsApi } from '../services/api';
import type { AgentInformation } from '../types/api';

export const useAgentStore = defineStore('agent', () => {
  const agents = ref<AgentInformation[]>([]);
  const selectedAgentId = ref<string | null>(null);
  const agentsLoading = ref(false);
  const agentsError = ref<string | null>(null);

  async function fetchAgents() {
    try {
      agentsLoading.value = true;
      agentsError.value = null;
      agents.value = await fetchAgentsApi();
    } catch (error) {
      console.error('Failed to load agents:', error);
      agentsError.value = error instanceof Error ? error.message : String(error);
      agents.value = [];
    } finally {
      agentsLoading.value = false;
    }
  }

  function setAgents(newAgents: AgentInformation[]) {
    agents.value = newAgents;
  }

  function setSelectedAgentId(id: string | null) {
    selectedAgentId.value = id;
  }

  function addAgent(agent: AgentInformation) {
    const exists = agents.value.some(existingAgent => existingAgent.id === agent.id);
    if (exists) {
      return;
    }

    agents.value = [...agents.value, agent];
  }

  return {
    agents,
    selectedAgentId,
    agentsLoading,
    agentsError,
    fetchAgents,
    setAgents,
    setSelectedAgentId,
    addAgent,
  };
});

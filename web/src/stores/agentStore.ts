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
import { compactAgentSession, fetchAgents as fetchAgentsApi } from '../services/api';
import type { AgentInformation } from '../types/api';

type SessionCompactionState =
  | { status: 'compacting' }
  | { status: 'completed' }
  | { status: 'error'; error: string };

export const useAgentStore = defineStore('agent', () => {
  const agents = ref<AgentInformation[]>([]);
  const sessionCompactionStates = ref<Record<string, SessionCompactionState>>({});
  const agentsLoading = ref(false);
  const agentsError = ref<string | null>(null);

  async function fetchAgents() {
    try {
      agentsLoading.value = true;
      agentsError.value = null;
      for (const agent of await fetchAgentsApi()) {
        addAgent(agent);
      }
    } catch (error) {
      console.error('Failed to load agents:', error);
      agentsError.value = error instanceof Error ? error.message : String(error);
      agents.value = [];
    } finally {
      agentsLoading.value = false;
    }
  }

  function addAgent(agent: AgentInformation) {
    const exists = agents.value.some(existingAgent => existingAgent.id === agent.id);
    if (exists) {
      console.warn(`Not adding agent with ID ${agent.id} which already exists.`)
      return;
    }
    agents.value = [...agents.value, agent];
  }

  async function compactSession(agentId: string) {
    if (sessionCompactionStates.value[agentId]?.status === 'compacting') {
      return;
    }

    sessionCompactionStates.value = {
      ...sessionCompactionStates.value,
      [agentId]: { status: 'compacting' },
    };

    try {
      await compactAgentSession(agentId);
      sessionCompactionStates.value = {
        ...sessionCompactionStates.value,
        [agentId]: { status: 'completed' },
      };
    } catch (error) {
      console.error(`Failed to compact session for agent ${agentId}:`, error);
      sessionCompactionStates.value = {
        ...sessionCompactionStates.value,
        [agentId]: {
          status: 'error',
          error: error instanceof Error ? error.message : String(error),
        },
      };
    }
  }

  return {
    agents,
    sessionCompactionStates,
    agentsLoading,
    agentsError,
    fetchAgents,
    compactSession,
    addAgent,
  };
});

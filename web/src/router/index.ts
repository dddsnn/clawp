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

import { createRouter, createWebHistory } from 'vue-router';
import AgentChatContainer from '../components/chat/AgentChatContainer.vue';
import AgentManagement from '../components/agent/AgentManagement.vue';
import ChannelDetailsContainer from '../components/channel/ChannelDetailsContainer.vue';
import CollectionLanding from '../components/layout/CollectionLanding.vue';
import PersonalityDetailsContainer from '../components/personality/PersonalityDetailsContainer.vue';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: CollectionLanding,
      props: { message: 'Select a section to view.' },
      meta: { collection: 'root' },
    },
    {
      path: '/agents',
      name: 'agents',
      component: CollectionLanding,
      props: { message: 'Select an agent to view.' },
      meta: { collection: 'agents' },
    },
    {
      path: '/agents/:agentId',
      name: 'agent',
      redirect: (to) => ({ name: 'agent-chat', params: { agentId: to.params.agentId } }),
    },
    {
      path: '/agents/:agentId/chat',
      name: 'agent-chat',
      component: AgentChatContainer,
      props: (route) => ({ agentId: route.params.agentId }),
      meta: { collection: 'agents' },
    },
    {
      path: '/agents/:agentId/management',
      name: 'agent-management',
      component: AgentManagement,
      props: (route) => ({ agentId: route.params.agentId }),
      meta: { collection: 'agents' },
    },
    {
      path: '/personalities',
      name: 'personalities',
      component: CollectionLanding,
      props: { message: 'Select a personality to view.' },
      meta: { collection: 'personalities' },
    },
    {
      path: '/personalities/:personalityName',
      name: 'personality-details',
      component: PersonalityDetailsContainer,
      props: (route) => ({ personalityName: route.params.personalityName }),
      meta: { collection: 'personalities' },
    },
    {
      path: '/channels',
      name: 'channels',
      component: CollectionLanding,
      props: { message: 'Select a channel to view.' },
      meta: { collection: 'channels' },
    },
    {
      path: '/channels/:channelType/:channelId',
      name: 'channel-details',
      component: ChannelDetailsContainer,
      props: (route) => ({ channelKey: `${route.params.channelType}:${route.params.channelId}` }),
      meta: { collection: 'channels' },
    },
  ],
});

export default router;

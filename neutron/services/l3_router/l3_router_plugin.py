# Copyright (c) 2013 OpenStack Foundation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from neutron_lib import constants as n_const
from oslo_config import cfg
from oslo_log import helpers as log_helpers
from oslo_utils import importutils

from neutron.api.rpc.agentnotifiers import l3_rpc_agent_api
from neutron.api.rpc.handlers import l3_rpc
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron.db import common_db_mixin
from neutron.db import dns_db
from neutron.db import extraroute_db
from neutron.db import l3_db
from neutron.db import l3_dvr_ha_scheduler_db
from neutron.db import l3_dvrscheduler_db
from neutron.db import l3_gwmode_db
from neutron.db import l3_hamode_db
from neutron.db import l3_hascheduler_db
from neutron.extensions import l3
from neutron.plugins.common import constants
from neutron.quota import resource_registry
from neutron import service
from neutron.services.l3_router.service_providers import driver_controller
from neutron.services import service_base


class L3RouterPlugin(service_base.ServicePluginBase,
                     common_db_mixin.CommonDbMixin,
                     extraroute_db.ExtraRoute_db_mixin,
                     l3_hamode_db.L3_HA_NAT_db_mixin,
                     l3_gwmode_db.L3_NAT_db_mixin,
                     l3_dvr_ha_scheduler_db.L3_DVR_HA_scheduler_db_mixin,
                     dns_db.DNSDbMixin):

    """Implementation of the Neutron L3 Router Service Plugin.

    This class implements a L3 service plugin that provides
    router and floatingip resources and manages associated
    request/response.
    All DB related work is implemented in classes
    l3_db.L3_NAT_db_mixin, l3_hamode_db.L3_HA_NAT_db_mixin,
    l3_dvr_db.L3_NAT_with_dvr_db_mixin, and extraroute_db.ExtraRoute_db_mixin.
    """
    supported_extension_aliases = ["dvr", "router", "ext-gw-mode",
                                   "extraroute", "l3_agent_scheduler",
                                   "l3-ha", "router_availability_zone",
                                   "l3-flavors"]

    __native_pagination_support = True
    __native_sorting_support = True

    @resource_registry.tracked_resources(router=l3_db.Router,
                                         floatingip=l3_db.FloatingIP)
    def __init__(self):
        self.router_scheduler = importutils.import_object(
            cfg.CONF.router_scheduler_driver)
        self.add_periodic_l3_agent_status_check()
        super(L3RouterPlugin, self).__init__()
        if 'dvr' in self.supported_extension_aliases:
            l3_dvrscheduler_db.subscribe()
        if 'l3-ha' in self.supported_extension_aliases:
            l3_hascheduler_db.subscribe()
        self.agent_notifiers.update(
            {n_const.AGENT_TYPE_L3: l3_rpc_agent_api.L3AgentNotifyAPI()})

        rpc_worker = service.RpcWorker([self], worker_process_count=0)

        self.add_worker(rpc_worker)
        self.l3_driver_controller = driver_controller.DriverController(self)

    @log_helpers.log_method_call
    def start_rpc_listeners(self):
        # RPC support
        self.topic = topics.L3PLUGIN
        self.conn = n_rpc.create_connection()
        self.endpoints = [l3_rpc.L3RpcCallback()]
        self.conn.create_consumer(self.topic, self.endpoints,
                                  fanout=False)
        return self.conn.consume_in_threads()

    @classmethod
    def get_plugin_type(cls):
        return constants.L3_ROUTER_NAT

    def get_plugin_description(self):
        """returns string description of the plugin."""
        return ("L3 Router Service Plugin for basic L3 forwarding"
                " between (L2) Neutron networks and access to external"
                " networks via a NAT gateway.")

    def create_floatingip(self, context, floatingip):
        """Create floating IP.

        :param context: Neutron request context
        :param floatingip: data for the floating IP being created
        :returns: A floating IP object on success

        As the l3 router plugin asynchronously creates floating IPs
        leveraging the l3 agent, the initial status for the floating
        IP object will be DOWN.
        """
        return super(L3RouterPlugin, self).create_floatingip(
            context, floatingip,
            initial_status=n_const.FLOATINGIP_STATUS_DOWN)


def add_flavor_id(plugin, router_res, router_db):
    router_res['flavor_id'] = router_db['flavor_id']


common_db_mixin.CommonDbMixin.register_dict_extend_funcs(
    l3.ROUTERS, [add_flavor_id])

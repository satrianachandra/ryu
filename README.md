What's Ryu
==========
Ryu is a component-based software defined networking framework.

Ryu provides software components with well defined API that make it
easy for developers to create new network management and control
applications. Ryu supports various protocols for managing network
devices, such as OpenFlow, Netconf, OF-config, etc. About OpenFlow,
Ryu supports fully 1.0, 1.2, 1.3, 1.4 and Nicira Extensions.

All of the code is freely available under the Apache 2.0 license. Ryu
is fully written in Python.


Quick Start
===========
Installing Ryu is quite easy::

   % pip install ryu

If you prefer to install Ryu from the source code::

   % git clone git://github.com/osrg/ryu.git
   % cd ryu; python ./setup.py install

If you want to use Ryu with `OpenStack <http://openstack.org/>`_,
please refer `detailed documents <http://ryu.readthedocs.org/en/latest/using_with_openstack.html>`_.
You can create tens of thousands of isolated virtual networks without
using VLAN.  The Ryu application is included in OpenStack mainline as
of Essex release.

If you want to write your Ryu application, have a look at
`Writing ryu application <http://ryu.readthedocs.org/en/latest/writing_ryu_app.html>`_ document.
After writing your application, just type::

   % ryu-manager yourapp.py


##REST API for Qos settings in CPQD ofsoftswitch

| Endpoint | Description |
| ---- | --------------- |
| [PUT /v1.0/conf/switches/{SWITCH_ID}](https://github.com/satrianachandra/ryu/wiki/REST-API-for-Ecology-Framework#put-confswitchesswitch_idunix_socket) | Set switch address |
| [POST /qos/queue/{SWITCH_ID}](https://github.com/satrianachandra/ryu/wiki/REST-API-for-Ecology-Framework#post-qosqueueswitch_id) | Set QoS settings with data : port-name, queues: min-rate: |
| [GET /qos/queue/{SWITCH_ID}](https://github.com/satrianachandra/ryu/wiki/REST-API-for-Ecology-Framework#get-qosqueueswitch_id) | Get all queues settings in the switch |
| [DELETE /qos/queue/{SWITCH_ID}](https://github.com/satrianachandra/ryu/wiki/REST-API-for-Ecology-Framework#delete-qosqueueswitch_id) | Delete all queues settings in the switch |
| [DELETE /qos/queue/{SWITCH_ID}/{PORT}/{QUEUE_ID}](https://github.com/satrianachandra/ryu/wiki/REST-API-for-Ecology-Framework#delete-qosqueueswitch_id) | Delete a specific queue |


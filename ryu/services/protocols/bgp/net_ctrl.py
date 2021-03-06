# Copyright (C) 2014 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
 Network Controller interface to BGP.

 Network controller w.r.t BGPS for APGW Automation project is named as APGW
 Agent and Route Server.
"""
import logging
import socket
import traceback

from ryu.services.protocols.bgp import api
from ryu.services.protocols.bgp.api.base import ApiException
from ryu.services.protocols.bgp.api.base import NEXT_HOP
from ryu.services.protocols.bgp.api.base import ORIGIN_RD
from ryu.services.protocols.bgp.api.base import PREFIX
from ryu.services.protocols.bgp.api.base import ROUTE_DISTINGUISHER
from ryu.services.protocols.bgp.api.base import VPN_LABEL
from ryu.services.protocols.bgp.base import Activity
from ryu.services.protocols.bgp.base import add_bgp_error_metadata
from ryu.services.protocols.bgp.base import BGPSException
from ryu.services.protocols.bgp.base import FlexinetPeer
from ryu.services.protocols.bgp.base import NET_CTRL_ERROR_CODE
from ryu.services.protocols.bgp.constants import VRF_TABLE
from ryu.services.protocols.bgp.rtconf.vrfs import VRF_RF
from ryu.services.protocols.bgp.rtconf.vrfs import VrfConf
from ryu.services.protocols.bgp.utils.validation import is_valid_ipv4


# Logger instance for this module.
LOG = logging.getLogger('bgpspeaker.net_ctrl')

# Network controller service socket constants.
NC_RPC_BIND_IP = 'apgw_rpc_bind_ip'
NC_RPC_BIND_PORT = 'apgw_rpc_bind_port'

# Notification symbols
NOTF_ADD_REMOTE_PREFX = 'prefix.add_remote'
NOTF_DELETE_REMOTE_PREFX = 'prefix.delete_remote'
NOTF_ADD_LOCAL_PREFX = 'prefix.add_local'
NOTF_DELETE_LOCAL_PREFX = 'prefix.delete_local'
NOTF_LOG = 'logging'

# MessagePackRPC message type constants
RPC_MSG_REQUEST = 0
RPC_MSG_RESPONSE = 1
RPC_MSG_NOTIFY = 2

#
# Indexes for various RPC message types.
#
RPC_IDX_MSG_TYP = 0
RPC_IDX_MSG_ID = 1
RPC_IDX_REQ_SYM = 2
RPC_IDX_REQ_PARAM = 3
RPC_IDX_RES_ERR = 2
RPC_IDX_RES_RST = 3
RPC_IDX_NTF_SYM = 1
RPC_IDX_NTF_PARAM = 2

# RPC socket receive buffer size in bytes.
RPC_SOCK_BUFF_SIZE = 4096


@add_bgp_error_metadata(code=NET_CTRL_ERROR_CODE,
                        sub_code=1,
                        def_desc='Unknown Network controller exception')
class NetworkControllerError(BGPSException):
    """Common base class for exceptions related to RPC calls.
    """
    pass


class RpcSession(Activity):
    """Provides message-pack RPC abstraction for one session.

    It contains message-pack packer, un-packer, message ID sequence
    and utilities that use these. It also cares about socket communication w/
    RPC peer.
    """

    def __init__(self, socket, outgoing_msg_sink_iter):
        super(RpcSession, self).__init__("RpcSession(%s)" % socket)
        import msgpack

        self._packer = msgpack.Packer()
        self._unpacker = msgpack.Unpacker()
        self._next_msgid = 0
        self._socket = socket
        self._outgoing_msg_sink_iter = outgoing_msg_sink_iter

    def stop(self):
        super(RpcSession, self).stop()
        LOG.critical(
            'RPC Session to %s stopped', str(self._socket.getpeername())
        )

    def _run(self):
        # Process outgoing messages in new thread.
        green_out = self._spawn('net_ctrl._process_outgoing',
                                self._process_outgoing_msg,
                                self._outgoing_msg_sink_iter)
        # Process incoming messages in new thread.
        green_in = self._spawn('net_ctrl._process_incoming',
                               self._process_incoming_msgs)
        LOG.critical(
            'RPC Session to %s started', str(self._socket.getpeername())
        )
        green_in.wait()
        green_out.wait()

    def _next_msg_id(self):
        this_id = self._next_msgid
        self._next_msgid += 1
        return this_id

    def create_request(self, method, params):
        msgid = self._next_msg_id()
        return self._packer.pack([RPC_MSG_REQUEST, msgid, method, params])

    def create_error_response(self, msgid, error):
        if error is None:
            raise NetworkControllerError(desc='Creating error without body!')
        return self._packer.pack([RPC_MSG_RESPONSE, msgid, error, None])

    def create_success_response(self, msgid, result):
        if result is None:
            raise NetworkControllerError(desc='Creating response without '
                                              'body!')
        return self._packer.pack([RPC_MSG_RESPONSE, msgid, None, result])

    def create_notification(self, method, params):
        return self._packer.pack([RPC_MSG_NOTIFY, method, params])

    def feed_and_get_messages(self, data):
        self._unpacker.feed(data)
        messages = []
        for msg in self._unpacker:
            messages.append(msg)
        return messages

    def feed_and_get_first_message(self, data):
        self._unpacker.feed(data)
        for msg in self._unpacker:
            return msg

    def send_notification(self, method, params):
        rpc_msg = self.create_notification(method, params)
        return self._sendall(rpc_msg)

    def _process_incoming_msgs(self):
        LOG.debug('NetworkController started processing incoming messages')
        assert self._socket

        while True:
            # Wait for request/response/notification from peer.
            msg_buff = self._recv()
            if len(msg_buff) == 0:
                LOG.info('Peer %r disconnected.' % self._socket)
                break
            messages = self.feed_and_get_messages(msg_buff)
            for msg in messages:
                if msg[0] == RPC_MSG_REQUEST:
                    try:
                        result = _handle_request(msg)
                        _send_success_response(self, self._socket, msg, result)
                    except BGPSException as e:
                        _send_error_response(self, self._socket, msg,
                                             e.message)
                elif msg[0] == RPC_MSG_RESPONSE:
                    _handle_response(msg)
                elif msg[0] == RPC_MSG_NOTIFY:
                    _handle_notification(msg)
                else:
                    LOG.error('Invalid message type: %r' % msg)
                self.pause(0)

    def _process_outgoing_msg(self, sink_iter):
        """For every message we construct a corresponding RPC message to be
        sent over the given socket inside given RPC session.

        This function should be launched in a new green thread as
        it loops forever.
        """
        LOG.debug('NetworkController processing outgoing request list.')
        # TODO(Team): handle un-expected exception breaking the loop in
        # graceful manner. Discuss this with other component developers.
        # TODO(PH): We should try not to sent routes from bgp peer that is not
        # in established state.
        from ryu.services.protocols.bgp.model import \
            FlexinetOutgoingRoute
        while True:
            # sink iter is Sink instance and next is blocking so this isn't
            # active wait.
            for outgoing_msg in sink_iter:
                if isinstance(outgoing_msg, FlexinetOutgoingRoute):
                    rpc_msg = _create_prefix_notif(outgoing_msg, self)
                else:
                    raise NotImplementedError(
                        'Do not handle out going message'
                        ' of type %s' %
                        outgoing_msg.__class__)
                if rpc_msg:
                    self._sendall(rpc_msg)
            self.pause(0)

    def _recv(self):
        return self._sock_wrap(self._socket.recv)(RPC_SOCK_BUFF_SIZE)

    def _sendall(self, msg):
        return self._sock_wrap(self._socket.sendall)(msg)

    def _sock_wrap(self, func):
        def wrapper(*args, **kwargs):
            try:
                ret = func(*args, **kwargs)
            except socket.error:
                LOG.error(traceback.format_exc())
                self._socket_error()
                return
            return ret

        return wrapper

    def _socket_error(self):
        if self.started:
            self.stop()


def _create_prefix_notif(outgoing_msg, rpc_session):
    """Constructs prefix notification with data from given outgoing message.

    Given RPC session is used to create RPC notification message.
    """
    assert(outgoing_msg)
    path = outgoing_msg.path
    assert(path)
    vpn_nlri = path.nlri

    rpc_msg = None
    assert path.source is not None
    if path.source != VRF_TABLE:
        # Extract relevant info for update-add/update-delete.
        params = [{ROUTE_DISTINGUISHER: outgoing_msg.route_dist,
                   PREFIX: vpn_nlri.prefix,
                   NEXT_HOP: path.nexthop,
                   VPN_LABEL: path.label_list[0],
                   VRF_RF: VrfConf.rf_2_vrf_rf(path.route_family)}]
        if not path.is_withdraw:
            # Create notification to NetworkController.
            rpc_msg = rpc_session.create_notification(NOTF_ADD_REMOTE_PREFX,
                                                      params)
        else:
            # Create update-delete request to NetworkController.`
            rpc_msg = rpc_session.create_notification(NOTF_DELETE_REMOTE_PREFX,
                                                      params)
    else:
        # Extract relevant info for update-add/update-delete.
        params = [{ROUTE_DISTINGUISHER: outgoing_msg.route_dist,
                   PREFIX: vpn_nlri.prefix,
                   NEXT_HOP: path.nexthop,
                   VRF_RF: VrfConf.rf_2_vrf_rf(path.route_family),
                   ORIGIN_RD: path.origin_rd}]
        if not path.is_withdraw:
            # Create notification to NetworkController.
            rpc_msg = rpc_session.create_notification(NOTF_ADD_LOCAL_PREFX,
                                                      params)
        else:
            # Create update-delete request to NetworkController.`
            rpc_msg = rpc_session.create_notification(NOTF_DELETE_LOCAL_PREFX,
                                                      params)

    return rpc_msg


def _validate_rpc_ip(rpc_server_ip):
    """Validates given ip for use as rpc host bind address.
    """
    if not is_valid_ipv4(rpc_server_ip):
        raise NetworkControllerError(desc='Invalid rpc ip address.')
    return rpc_server_ip


def _validate_rpc_port(port):
    """Validates give port for use as rpc server port.
    """
    if not port:
        raise NetworkControllerError(desc='Invalid rpc port number.')
    if isinstance(port, str):
        port = int(port)

    if port <= 0:
        raise NetworkControllerError(desc='Invalid rpc port number %s' % port)
    return port


class _NetworkController(FlexinetPeer, Activity):
    """Network controller peer.

    Provides MessagePackRPC interface for flexinet peers like Network
    controller to peer and have RPC session with BGPS process. This RPC
    interface provides access to BGPS API.
    """

    def __init__(self):
        FlexinetPeer.__init__(self)
        Activity.__init__(self, name='NETWORK_CONTROLLER')
        # Outstanding requests, i.e. requests for which we are yet to receive
        # response from peer. We currently do not have any requests going out.
        self._outstanding_reqs = {}
        self._rpc_session = None

    def _run(self, *args, **kwargs):
        """Runs RPC server.

        Wait for peer to connect and start rpc session with it.
        For every connection we start and new rpc session.
        """
        apgw_rpc_bind_ip = _validate_rpc_ip(kwargs.pop(NC_RPC_BIND_IP))
        apgw_rpc_bind_port = _validate_rpc_port(kwargs.pop(NC_RPC_BIND_PORT))

        sock_addr = (apgw_rpc_bind_ip, apgw_rpc_bind_port)
        LOG.debug('NetworkController started listening for connections...')

        server_thread, socket = self._listen_tcp(sock_addr,
                                                 self._start_rpc_session)
        self.pause(0)
        server_thread.wait()

    def _start_rpc_session(self, socket):
        """Starts a new RPC session with given connection.
        """
        if self._rpc_session and self._rpc_session.started:
            self._rpc_session.stop()

        self._rpc_session = RpcSession(socket, self)
        self._rpc_session.start()

    def send_rpc_notification(self, method, params):
        if (self.started and self._rpc_session is not None and
                self._rpc_session.started):
            return self._rpc_session.send_notification(method, params)


def _handle_response(response):
    raise NotImplementedError('BGPS is not making any request hence should not'
                              ' get any response. Response: %s' % response)


def _handle_notification(notification):
    LOG.debug('Notification from NetworkController<<: %s %s' %
              (notification[RPC_IDX_NTF_SYM], notification[RPC_IDX_NTF_PARAM]))
    operation, params = notification[1], notification[2]
    return api.base.call(operation, **params[0])


def _handle_request(request):
    LOG.debug('Request from NetworkController<<: %s %s' %
              (request[RPC_IDX_REQ_SYM], request[RPC_IDX_REQ_PARAM]))
    operation, params = request[2], request[3]
    kwargs = {}
    if len(params) > 0:
        kwargs = params[0]
    try:
        return api.base.call(operation, **kwargs)
    except TypeError:
        LOG.error(traceback.format_exc())
        raise ApiException(desc='Invalid type for RPC parameter.')


def _send_success_response(rpc_session, socket, request, result):
    response = rpc_session.create_success_response(request[RPC_IDX_MSG_ID],
                                                   result)
    socket.sendall(response)


def _send_error_response(rpc_session, socket, request, emsg):
    response = rpc_session.create_error_response(request[RPC_IDX_MSG_ID],
                                                 str(emsg))
    socket.sendall(response)


# Network controller singleton
NET_CONTROLLER = _NetworkController()

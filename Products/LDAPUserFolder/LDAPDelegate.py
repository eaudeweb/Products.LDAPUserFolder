##############################################################################
#
# Copyright (c) 2000-2009 Jens Vagelpohl and Contributors. All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
""" LDAPDelegate: A delegate that performs LDAP operations

$Id$
"""

import ldap
import logging
import random

from AccessControl.SecurityManagement import getSecurityManager
from Persistence import Persistent
from zope.component import getUtility
from zope.component.factory import Factory
from zope.component.interfaces import ComponentLookupError
from zope.component.interfaces import IFactory

from dataflake.ldapconnection.connection import LDAPConnection

from Products.LDAPUserFolder.SharedResource import getResource
from Products.LDAPUserFolder.SharedResource import setResource

logger = logging.getLogger('event.LDAPDelegate')


class LDAPDelegate(Persistent):
    """ LDAPDelegate

    This object handles all LDAP operations. All search operations will
    return a dictionary, where the keys are as follows:

    exception   - Contains a string representing exception information
                  if an exception was raised during the operation.

    size        - An integer containing the length of the result set
                  generated by the operation. Will be 0 if an exception
                  was raised.

    results     - Sequence of results
    """

    def __init__( self, server='', login_attr='', users_base='', rdn_attr=''
                , use_ssl=0, bind_dn='', bind_pwd='', read_only=0
                ):
        """ Create a new LDAPDelegate instance """
        self._hash = 'ldap_delegate%s' % str(random.random())
        self._servers = []
        self.edit( login_attr, users_base, rdn_attr
                 , 'top,person', bind_dn, bind_pwd
                 , 1, read_only
                 )

        if server != '':
            if server.find(':') != -1:
                host = server.split(':')[0].strip()
                port = int(server.split(':')[1])
            else:
                host = server

                if use_ssl == 2:
                    port = 0
                elif use_ssl == 1:
                    port = 636
                else:
                    port = 389

            self.addServer(host, port, use_ssl)

    def addServer( self
                 , host
                 , port='389'
                 , use_ssl=0
                 , conn_timeout=-1
                 , op_timeout=-1
                 ):
        """ Add a server to our list of servers """
        servers = self.getServers()

        if use_ssl == 2:
            protocol = 'ldapi'
            port = 0
        elif use_ssl == 1:
            protocol = 'ldaps'
        else:
            protocol = 'ldap'

        existing = [x for x in servers if x['host']==host and x['port']==port]
        if not existing:
            servers.append( { 'host' : host
                            , 'port' : port
                            , 'protocol' : protocol
                            , 'conn_timeout' : conn_timeout
                            , 'op_timeout' : op_timeout
                            } )
            self._servers = servers

        # Delete the cached connection in case the new server was added
        # in response to the existing server failing in a way that leads
        # to nasty timeouts
        setResource('%s-connection' % self._hash, '')

    def getServers(self):
        """ Return info about all my servers """
        servers = getattr(self, '_servers', [])

        if isinstance(servers, dict):
            servers = servers.values()
            self._servers = servers

        return servers

    def deleteServers(self, position_list=()):
        """ Delete server definitions """
        old_servers = self.getServers()
        new_servers = []
        position_list = [int(x) for x in position_list]

        for i in range(len(old_servers)):
            if i not in position_list:
                new_servers.append(old_servers[i])

        self._servers = new_servers

        # Delete the cached connection so that we don't accidentally
        # continue using a server we should not be using anymore
        setResource('%s-connection' % self._hash, '')

    def edit( self, login_attr, users_base, rdn_attr, objectclasses
            , bind_dn, bind_pwd, binduid_usage, read_only
            ):
        """ Edit this LDAPDelegate instance """
        self.login_attr = login_attr
        self.rdn_attr = rdn_attr
        self.bind_dn = bind_dn
        self.bind_pwd = bind_pwd
        self.binduid_usage = int(binduid_usage)
        self.read_only = not not read_only
        self.u_base = users_base

        if isinstance(objectclasses, str) or isinstance(objectclasses, unicode):
            objectclasses = [x.strip() for x in objectclasses.split(',')]
        self.u_classes = objectclasses

    def connect(self, bind_dn='', bind_pwd=''):
        """ Initialize an ldap server connection 
        """
        if bind_dn != '':
            user_dn = bind_dn
            user_pwd = bind_pwd or '~'
        elif self.binduid_usage == 1:
            user_dn = self.bind_dn
            user_pwd = self.bind_pwd
        else:
            user = getSecurityManager().getUser()
            try:
                user_dn = user.getUserDN()
                user_pwd = user._getPassword()
            except AttributeError:
                user_dn = user_pwd = ''

        connection_manager = getResource('%s-connection' % self._hash, str, ())
        if connection_manager._type() is not LDAPConnection:

            if not self._servers:
                raise RuntimeError('No servers defined')

            for i in range(len(self._servers)):
                svr = self._servers[i]
                if not i:
                    try:
                        c_factory = getUtility( IFactory
                                              , 'LDAP connection factory'
                                              )
                    except ComponentLookupError:
                        c_factory = ldap.ldapobject.ReconnectLDAPObject

                    connection_manager = LDAPConnection( svr['host']
                                             , svr['port']
                                             , svr['protocol']
                                             , c_factory=c_factory
                                             , rdn_attr=self.rdn_attr
                                             , read_only=self.read_only
                                             , conn_timeout=svr['conn_timeout']
                                             , op_timeout=svr['op_timeout']
                                             )
            else:
                connection_manager.addServer( svr['host']
                                            , svr['port']
                                            , svr['protocol']
                                            , conn_timeout=svr['conn_timeout']
                                            , op_timeout=svr['op_timeout']
                                            )

        if isinstance(connection_manager, LDAPConnection):
            setResource('%s-connection' % self._hash, connection_manager)

        connection_manager.connect(user_dn, user_pwd)
        return connection_manager

    def search( self
              , base
              , scope
              , fltr='(objectClass=*)'
              , attrs=[]
              , bind_dn=''
              , bind_pwd=''
              , convert_filter=True
              ):
        """ The main search engine """
        connection_manager = self.connect()
        return connection_manager.search( base
                                        , scope
                                        , fltr=fltr
                                        , attrs=attrs
                                        , bind_dn=bind_dn
                                        , bind_pwd=bind_pwd
                                        , convert_filter=convert_filter
                                        )

    def insert(self, base, rdn, attrs=None):
        """ Insert a new record """
        self._complainIfReadOnly()
        connection_manager = self.connect()
        return connection_manager.insert(base, rdn, attrs=attrs)

    def delete(self, dn):
        """ Delete a record """
        self._complainIfReadOnly()
        connection_manager = self.connect()
        return connection_manager.delete(dn)

    def modify(self, dn, mod_type=None, attrs=None):
        """ Modify a record """
        self._complainIfReadOnly()
        connection_manager = self.connect()
        return connection_manager.modify(dn, mod_type=mod_type, attrs=attrs)

    def _complainIfReadOnly(self):
        """ Raise RuntimeError if the connection is set to `read-only`

        This method should be called before any directory tree modfication
        """
        if self.read_only:
            raise RuntimeError(
                'Running in read-only mode, directory modifications disabled')

    # Some helper functions and constants that are now on the LDAPDelegate
    # object itself to make it easier to override in subclasses, paving
    # the way for different kinds of delegates.

    ADD = ldap.MOD_ADD
    DELETE = ldap.MOD_DELETE
    REPLACE = ldap.MOD_REPLACE
    BASE = ldap.SCOPE_BASE

    def getScopes(self):
        return (ldap.SCOPE_BASE, ldap.SCOPE_ONELEVEL, ldap.SCOPE_SUBTREE)

connectionFactory = Factory(ldap.ldapobject.ReconnectLDAPObject)


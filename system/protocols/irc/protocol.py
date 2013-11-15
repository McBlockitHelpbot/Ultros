# coding=utf-8

import time
import logging

from system.protocols.irc.channel import Channel
from system.protocols.irc.rank import Rank, Ranks
from system.protocols.irc.user import User
from utils.irc import IRCUtils

from utils.log import getLogger
from utils.misc import output_exception
from system.event_manager import EventManager
from system.events import irc as irc_events

from twisted.words.protocols import irc
from twisted.internet import reactor

from kitchen.text.converters import to_bytes


class Protocol(irc.IRCClient):
    factory = None
    config = None
    log = None
    event_manger = None

    networking = {}
    identity = {}

    nickname = ""

    channels = {}  # key is lowercase "#channel" - use get/set/del_channel()
    users = []

    ssl = False

    def __init__(self, factory, config):
        self.log = getLogger("IRC")
        self.log.info("Setting up..")

        try:
            from twisted.internet import ssl
            raise ImportError("For testing")
        except ImportError:
            ssl = False
            self.ssl = False
            self.log.warn("Unable to import the SSL library. "
                          "SSL will not be available.")

            output_exception(self.log, logging.WARN)
        else:
            self.ssl = True

        self.factory = factory
        self.config = config
        self.event_manager = EventManager.instance()
        self.utils = IRCUtils(self.log)
        # Three dicts for easier lookup
        self.ranks = Ranks()
        # Default prefixes in case the server doesn't send us a RPL_ISUPPORT
        self.ranks.add_rank("o", "@", 0)
        self.ranks.add_rank("v", "+", 1)

        self.networking = config["network"]
        self.identity = config["identity"]

        if self.identity["authentication"].lower() == "password":
            self.password = "%s:%s" % (self.identity["auth_name"],
                                       self.identity["auth_pass"])

        self.nickname = self.identity["nick"]

        # TODO: Throw event (General, pre-connection)

        if self.networking["ssl"] and not self.ssl:
            self.log.error("SSL is not available but was requested in the "
                           "configuration.")
            self.log.error("IRC will be unavailable until SSL is fixed or is "
                           "disabled in the configuration.")

            self.factory.manager.remove_protocol("irc")
            del self.factory
            return

        if self.networking["ssl"]:
            self.log.debug("Connecting with SSL")
            reactor.connectSSL(
                self.networking["address"],
                self.networking["port"],
                self.factory,
                ssl.ClientContextFactory(),
                120
            )
        else:
            self.log.debug("Connecting without SSL")
            reactor.connectTCP(
                self.networking["address"],
                self.networking["port"],
                self.factory,
                120
            )

        # TODO: Throw event (General, post-connection, pre-setup)

    def send_unicode_line(self, data):
        self.sendLine(to_bytes(data))

    def send_unicode_msg(self, target, data, length=None):
        self.msg(to_bytes(target), to_bytes(data), length)

    def receivedMOTD(self, motd):
        """ Called when we receive the MOTD. """
        self.log.info(" ===   MOTD   === ")
        for line in motd:
            self.log.info(line)
        self.log.info(" === END MOTD ===")

        event = irc_events.MOTDReceivedEvent(self, motd)
        self.event_manager.run_callback("IRC/MOTDReceived", event, True)

    def signedOn(self):
        """
         Called once we've connected and done our handshake with the IRC server
        """

        def do_sign_on(self):
            if self.identity["authentication"].lower() == "nickserv":
                self.msg(self.identity["auth_target"],
                         "IDENTIFY %s %s" % (self.identity["auth_name"],
                                             self.identity["auth_pass"]))
            elif self.identity["authentication"].lower() == "ns-old":
                self.msg(self.identity["auth_target"],
                         "IDENTIFY %s" % self.identity["auth_pass"])
            elif self.identity["authentication"].lower() == "auth":
                self.sendLine("AUTH %s %s" % (
                    self.identity["auth_name"], self.identity["auth_pass"]))
            elif self.identity["authentication"].lower() == "password":
                self.sendLine("PASS %s:%s" % (
                    self.identity["auth_name"], self.identity["auth_pass"]))

        def do_channel_joins(self):
            for channel in self.config["channels"]:
                self.join(channel["name"], channel["key"])

            # TODO: Throw event (General, post-setup)

        self.log.debug(
            "Scheduling Deferreds for signing on and joining channels")

        reactor.callLater(5, do_sign_on, self)
        reactor.callLater(10, do_channel_joins, self)

        # TODO: Throw event (General, pre-setup)

    def joined(self, channel):
        """ Called when we join a channel. """
        self.log.info("Joined channel: %s" % channel)
        chan_obj = Channel(self, channel)
        self.set_channel(channel, chan_obj)
        # User-tracking stuff
        self.send_who(channel)

        # TODO: Use channel object in event
        event = irc_events.ChannelJoinedEvent(self, channel)
        self.event_manager.run_callback("IRC/ChannelJoined", event)

    def left(self, channel):
        """ Called when we part a channel.
        This could include opers using /sapart. """
        self.log.info("Parted channel: %s" % channel)
        chan_obj = self.get_channel(channel)
        # User-tracking stuff:
        self.self_part_channel(chan_obj)

        event = irc_events.ChannelPartedEvent(self, channel)
        self.event_manager.run_callback("IRC/ChannelParted", event)

    def privmsg(self, user, channel, message):
        """ Called when we receive a message - channel or private. """
        self.log.info("<%s:%s> %s" % (user, channel, message))
        user_obj = None
        try:
            user_obj = self._get_user_from_user_string(user)
        except:
            # Privmsg from the server itself and things (if that happens)
            self.log.debug("Notice from irregular user: %s" % user)
            user_obj = User(self, nickname=user, is_tracked=False)

        # TODO: Remove this piece of debugging code:
        if message[0] == "!":
            split = message.split(" ")
            command = split[0][1:].lower()
            args = split[1:]
            if command == "breakpoint":
                foo = "There is a breakpoint on this line for rakiru"
            elif command == "users":
                for user in self.users:
                    print user
                    for chan, ranks in user._ranks.iteritems():
                        print chan
                        for rank in ranks:
                            print rank
            elif command == "channels":
                for channel in self.channels:
                    print channel
                    print channel._modes

        # TODO: Throw event (General, received message - normal, [target])

    def noticed(self, user, channel, message):
        """ Called when we receive a notice - channel or private. """
        self.log.info("-%s:%s- %s" % (user, channel, message))
        user_obj = None
        try:
            user_obj = self._get_user_from_user_string(user)
        except:
            # Notices from the server itself and things
            self.log.debug("Notice from irregular user: %s" % user)
            user_obj = User(self, nickname=user, is_tracked=False)

        # TODO: Throw event (General, received message - notice, [target])

    def ctcpQuery(self, user, me, messages):
        """ Called when someone does a CTCP query - channel or private.
        Needs some param analysis."""
        self.log.info("[%s] %s" % (user, messages))
        user_obj = None
        try:
            user_obj = self._get_user_from_user_string(user)
        except:
            # CTCP from the server itself and things (if that happens)
            self.log.debug("CTCP from irregular user: %s" % user)
            user_obj = User(self, nickname=user, is_tracked=False)

        # TODO: Throw event (IRC, CTCP query)

    def modeChanged(self, user, channel, action, modes, args):
        """
        Called when someone changes a mode. Action is a bool specifying
        whether the mode was being set or unset.
        If it's a usermode, channel is the user being changed.

        Note: If it's a user-mode, channel_obj is set to None.
        """
        self.log.info("%s sets mode %s: %s%s %s" % (
            user, channel, "+" if action else "-", modes, args))

        # Get user/channel objects
        try:
            user_obj = self._get_user_from_user_string(user)
        except:
            # Mode change from the server itself and things
            self.log.debug("Mode change from irregular user: %s" % user)
            user_obj = User(self, nickname=user, is_tracked=False)
        channel_obj = None
        if not self.utils.compare_nicknames(self.nickname, channel):
            channel_obj = self.get_channel(channel)

        # Handle the mode changes
        for x in xrange(len(modes)):
            if channel_obj is None:
                # User mode (almost definitely always ourself)
                # TODO: Handle this (usermodes)
                pass
            elif modes[x] in self.ranks.modes:
                # Rank channel mode
                user_obj = self.get_user(args[x])
                if user_obj:
                    rank = self.ranks.by_mode(modes[x])
                    if action:
                        user_obj.add_rank_in_channel(channel, rank)
                    else:
                        user_obj.remove_rank_in_channel(channel, rank)
                else:
                    self.log.warning(
                        "Rank mode %s set on invalid user %s in channel %s"
                        % (modes[x], args[x], channel))
            else:
                # Other channel mode
                if action:
                    channel_obj.set_mode(modes[x], args[x])
                else:
                    channel_obj.remove_mode(modes[x])

        # TODO: Throw event (IRC, mode changed)

    def kickedFrom(self, channel, kicker, message):
        """ Called when we get kicked from a channel. """
        self.log.info("Kicked from %s by %s: %s" % (channel, kicker, message))
        chan_obj = self.get_channel(channel)
        # User-tracking stuff:
        self.self_part_channel(chan_obj)

        # TODO: Throw event (IRC, kicked from channel)

    def nickChanged(self, nick):
        """ Called when our nick is forcibly changed. """
        self.log.info("Nick changed to %s" % nick)

        # TODO: Throw event (General, name changed)

    def userJoined(self, user, channel):
        """ Called when someone else joins a channel we're in. """
        self.log.info("%s joined %s" % (user, channel))

        # TODO: Throw event (IRC, user joined channel)

    def irc_JOIN(self, prefix, params):
        """ Called on any join message
        :param prefix: The user joining
        :param params: The channel(s?) joined
        """
        irc.IRCClient.irc_JOIN(self, prefix, params)
        # For some reason, userJoined only gives the user's nick
        nickname, ident, host = self.utils.split_hostmask(prefix)
        if not self.utils.compare_nicknames(nickname, self.nickname):
            for chan in params:
                chan = self.get_channel(chan)
                self.user_join_channel(nickname, ident, host, chan)

    def userLeft(self, user, channel):
        """ Called when someone else leaves a channel we're in. """
        self.log.info("%s parted %s" % (user, channel))
        chan_obj = self.get_channel(channel)
        user_obj = self.get_user(nickname=user)
        # User-tracking stuff
        self.user_channel_part(user_obj, chan_obj)

        # TODO: Throw event (IRC, user left channel)

    def userKicked(self, kickee, channel, kicker, message):
        """ Called when someone else is kicked from a channel we're in. """
        self.log.info("%s was kicked from %s by %s: %s" % (
            kickee, channel, kicker, message))
        kickee_obj = self.get_user(nickname=kickee)
        # User-tracking stuff
        self.user_channel_part(kickee_obj, channel)

        # TODO: Throw event (IRC, user kicked from channel)

    def irc_QUIT(self, user, params):
        """ Called when someone else quits IRC. """
        quitmessage = params[0]
        self.log.info("%s has left IRC: %s" % (user, quitmessage))
        # User-tracking stuff
        user_obj = self.get_user(fullname=user)
        temp_chans = set(user_obj.channels)
        for channel in temp_chans:
            self.user_channel_part(user_obj, channel)

        # TODO: Throw event (General, user disconnected)

    def topicUpdated(self, user, channel, newTopic):
        """ Called when the topic is updated in a channel -
        also called when we join a channel. """
        self.log.info(
            "Topic for %s: %s (set by %s)" % (channel, newTopic, user))

        # TODO: Throw event (IRC, topic updated)

    def irc_NICK(self, prefix, params):
        """ Called when someone changes their nick.
        Surprisingly, twisted doesn't have a handler for this. """

        oldnick = prefix.split("!", 1)[0]
        newnick = params[0]

        user_obj = self.get_user(nickname=oldnick)
        user_obj.nickname = newnick

        self.log.info("%s is now known as %s" % (oldnick, newnick))

        # TODO: Throw event (General, user changed their nick)

    def irc_RPL_WHOREPLY(self, *nargs):
        """ Called when we get a WHO reply from the server.
        I'm seriously wondering if we even need this. """
        data_ = nargs[1]

        channel = data_[1]
        ident = data_[2]  # Starts with a ~ if there's no identd present
        host = data_[3]
        server = data_[4]
        nick = data_[5]
        status = data_[6]  # .strip("G").strip("H").strip("*")
        gecos = data_[7]  # Hops, realname

        # User-tracking stuff
        try:
            chan_obj = self.get_channel(channel)
            self.channel_who_response(nick,
                                      ident,
                                      host,
                                      server,
                                      status,
                                      gecos,
                                      chan_obj)
        except KeyError:
            # We got a WHO reply for a channel we're not in - doesn't matter
            # - for user-tracking purposes.
            pass

        # TODO: Throw event (IRC, WHO reply)

    def irc_RPL_ENDOFWHO(self, *nargs):
        """ Called when the server's done spamming us with WHO replies. """
        data_ = nargs[1]
        channel = data_[1]

        # TODO: Throw event (IRC, end of WHO reply)

    def irc_RPL_ISUPPORT(self, prefix, params):
        irc.IRCClient.irc_RPL_ISUPPORT(self, prefix, params)
        for param in params[1:-1]:
            self.log.debug("RPL_ISUPPORT received: %s" % param)
            prm = param.split("=")[0].strip("-")
            # prm is the param changed - don't bother parsing the value since
            # the it can be grabbed from self.supported with this:
            # self.supported.getFeature(prm)
            if prm == "CASEMAPPING":
                self.utils.case_mapping =\
                    self.supported.getFeature("CASEMAPPING")[0]  # Tuple
            elif prm == "PREFIX":
                # Remove the default prefixes before storing the new ones
                self.ranks = Ranks()
                for k, v in self.supported.getFeature("PREFIX").iteritems():
                    self.ranks.add_rank(k, v[0], v[1])
            # TODO: Throw event?

    def irc_unknown(self, prefix, command, params):
        """ Packets that aren't handled elsewhere get passed to this function.
        """

        if command == "RPL_BANLIST":
            # This is a single entry in a channel's ban list.
            channel = params[1]
            mask = params[2]
            owner = params[3]
            btime = params[4]

        # TODO: Throw event (IRC, ban list)

        elif command == "RPL_ENDOFBANLIST":
            # Called when the server's done spamming us with the ban list
            channel = params[1]

        # TODO: Throw event (IRC, end of ban list)

        elif command == "RPL_NAMREPLY":
            # This is the response to a NAMES request.
            # Also includes some data that has nothing to do with channel names
            me, status, channel, names = params
            users = names.split()
            if status == "@":  # Secret channel
                pass
            elif status == "*":  # Private channel
                pass

        # TODO: Throw event (IRC, NAMES reply)

        elif command == "RPL_ENDOFNAMES":
            # Called when the server's done spamming us with NAMES replies.
            me, channel, message = params

        # TODO: Throw event (IRC, end of NAMES reply)

        elif command == "ERR_INVITEONLYCHAN":
            self.log.warn(
                "Unable to join %s - Channel is invite-only" % params[1])

        # TODO: Throw event (IRC, channel is invite-only)

        elif str(command) == "972":  # ERR_CANNOTDOCOMMAND
            pass  # Need to analyze the args of this.
            # Called when some command we attempted can't be done.

        # TODO: Throw event (IRC, cannot do command)

        elif str(command) == "333":  # Channel creation details
            self.log.info("%s created by %s (%s)" %
                          (params[1],
                           params[2],
                           time.strftime(
                               "%a, %d %b %Y %H:%M:%S",
                               time.localtime(
                                   float(params[3])
                               ))
                           ))

        # TODO: Throw event (IRC, channel creation details)

        elif str(command) in ["265", "266"]:  # RPL_LOCALUSERS, RPL_GLOBALUSERS
            self.log.info(params[
                3])  # Usually printed, these are purely informational

        # TODO: Throw event (IRC, LOCALUSERS reply and GLOBALUSERS reply)

        elif str(command) == "396":  # VHOST was set
            self.log.info("VHOST set to %s by %s" % (params[1], prefix))

        # TODO: Throw event (IRC, VHOST set)

        elif command == "PONG":
            pass  # Do we really need to print these?

        # TODO: Throw event (IRC, PONG) - Debatable

        else:
            self.log.debug(
                "Unhandled: %s | %s | %s" % (prefix, command, params))

            # TODO: Throw event (IRC, unhandled message event based on command)

    def send_who(self, mask, operators_only=False):
        query = "WHO %s" % mask
        if operators_only:
            query += " o"
        #TODO: Use rate-limited wrapping function for sending
        self.sendLine(query)

    def _get_user_from_user_string(self, user_string, create_temp=True):
        nick, ident, host = self.utils.split_hostmask(user_string)
        user = self.get_user(nickname=nick, ident=ident, host=host)
        if user is None and create_temp:
            user = User(self, nick, ident, host, is_tracked=False)
        return user

    def get_user(self, *args, **kwargs):
        try:
            return self.get_users(*args, **kwargs)[0]
        except IndexError:
            return None

    def get_users(self, nickname=None, ident=None, host=None, fullname=None,
                  hostmask=None):
        matches = []
        if fullname:
            try:
                nickname, ident, host = self.utils.split_hostmask(fullname)
            except:
                return None
        if ident:
            ident = ident.lower()
        if host:
            host = host.lower()
        for user in self.users:
            if (nickname and
                    not self.utils.compare_nicknames(nickname, user.nickname)):
                continue
            if ident and ident != user.ident.lower():
                continue
            if host and host != user.host.lower():
                continue
            if (hostmask and
                    not self.utils.match_hostmask(user.fullname, hostmask)):
                continue
            matches.append(user)
        return matches

    def get_channel(self, channel):
        channel = self.utils.lowercase_nick_chan(channel)
        try:
            return self.channels[channel]
        except KeyError:
            return None

    def set_channel(self, channel, channel_obj):
        channel = self.utils.lowercase_nick_chan(channel)
        self.channels[channel] = channel_obj

    def del_channel(self, channel):
        channel = self.utils.lowercase_nick_chan(channel)
        del self.channels[channel]

    def self_part_channel(self, channel):
        for user in channel.users:
            self.user_channel_part(user, channel)
        self.del_channel(channel)

    def user_join_channel(self, nickname, ident, host, channel):
        user = self.get_user(nickname=nickname, ident=ident, host=host)
        if user is None:
            user = User(self, nickname, ident, host, is_tracked=True)
            self.users.append(user)
        user.add_channel(channel)
        channel.add_user(user)
        # For convenience
        return user

    def channel_modes_response(self, channel, modes):
        self.log.debug("Modes for %s: %s" % (channel, modes))
        pass

    def channel_who_response(self, nickname, ident, host, server, status,
                             gecos, channel):
        """User-tracking related
        :type channel: Channel
        """
        # If the user is not known about, create them.
        user = self.get_user(nickname=nickname, ident=ident, host=host)
        if user is None:
            user = self.user_join_channel(nickname, ident, host, channel)
        if not (channel in user.channels and user in channel.users):
            user.add_channel(channel)
            channel.add_user(user)
        for s in status:
            if s in "HG":
                # Here/Gone
                # TODO: Handle H/G status?
                pass
            elif s == "*":
                user.is_oper = True
            elif s in self.ranks.symbols:
                rank = self.ranks.by_symbol(s)
                user.add_rank_in_channel(channel, rank)
            else:
                # A bunch of ircd-specific stuff can appear here. We have no
                # need for it, but plugins could listen for WHO replies
                # themselves if they really need, or we can add stuff if a
                # proper use/specification is given.
                self.log.debug(
                    "Unexpected status in WHO response for user %s: %s" %
                    (user, s))
        user.realname = gecos.split(" ")[-1]

    def user_channel_part(self, user, channel):
        """User-tracking related
        :type channel: Channel
        """
        if not isinstance(user, User):
            user = self.get_user(nickname=user)
        # Remove user from channel and channel from user
        user.remove_channel(channel)
        channel.remove_user(user)
        # Check if they've gone off our radar
        self.user_check_lost_track(user)

    def user_check_lost_track(self, user):
        """User-tracking related"""
        if len(user.channels) == 0:
            self.log.debug("Lost track of user: %s" % user)
            self.users.remove(user)
            user.is_tracked = False
            # TODO: Throw event: lost track of user

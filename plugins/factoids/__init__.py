from twisted.internet import defer

from system.event_manager import EventManager
from system.protocols.generic.channel import Channel
from system.storage.formats import DBAPI
from system.storage.manager import StorageManager
from system.command_manager import CommandManager
from system.plugin import PluginObject

__author__ = 'Sean'

# Remember kids:
# * Stay in drugs
# * Eat your school
# * Don't do vegetables


class Plugin(PluginObject):

    CHANNEL = "channel"
    PROTOCOL = "protocol"
    GLOBAL = "global"

    PERM_ADD = "factoids.add.%s"
    PERM_SET = "factoids.set.%s"
    PERM_DEL = "factoids.delete.%s"
    PERM_GET = "factoids.get.%s"

    (RES_INVALID_LOCATION,
     RES_INVALID_METHOD,  # _FOR_LOCATION - i.e. CHANNEL in PM
     RES_NO_PERMS,
     RES_MISSING_FACTOID) = xrange(4)

    commands = None
    config = None
    db = None
    events = None
    storage = None

    def setup(self):
        ### Grab important shit
        self.commands = CommandManager()
        self.events = EventManager()
        self.storage = StorageManager()

        ### Set up database
        self.db = self.storage.get_file(self,
                                        "data",
                                        DBAPI,
                                        "sqlite3:data/plugins/factoids.sqlite",
                                        "data/plugins/factoids.sqlite")
        with self.db as db:
            db.runQuery("CREATE TABLE IF NOT EXISTS factoids ("
                        "factoid_key TEXT, "
                        "location TEXT, "
                        "factoid_name TEXT, "
                        "info TEXT, "
                        "UNIQUE(factoid_key, location) "
                        "ON CONFLICT REPLACE)")

        ### Register commands
        # We have multiple possible permissions per command, so we have to do
        # permission handling ourselves
        self.commands.register_command("addfactoid",
                                       self.factoid_add_command,
                                       self,
                                       None)
        self.commands.register_command("setfactoid",
                                       self.factoid_set_command,
                                       self,
                                       None)
        self.commands.register_command("deletefactoid",
                                       self.factoid_delete_command,
                                       self,
                                       None)
        self.commands.register_command("getfactoid",
                                       self.factoid_get_command,
                                       self,
                                       None)
        # TODO: Replace commands below with aliases when implemented
        self.commands.register_command("delfactoid",
                                       self.factoid_delete_command,
                                       self,
                                       None)

        ### Register events
        self.events.add_callback("MessageReceived",
                                 self,
                                 self.message_handler,
                                 1)

    # region Util functions

    def __check_perm(self, perm, caller, source, protocol):
        self.logger.debug("Checking for perm: '%s'", perm)
        # The second check is a hack to check default group, since there is not
        # currently inheritance
        # TODO: Remove this hack once inheritance has been added
        # Once this hack is not needed, this method can be removed and every
        # call can use the permission handler directly
        allowed = self.commands.perm_handler.check(perm,
                                                   caller,
                                                   source,
                                                   protocol)
        if not allowed:
            allowed = self.commands.perm_handler.check(perm,
                                                       None,
                                                       source,
                                                       protocol)
        return allowed

    def _parse_args(self, raw_args):
        """
        Grabs the location, factoid name, and info from a raw_args string
        """
        pos = raw_args.find(" ")
        if pos < 0:
            raise ValueError("Invalid args")
        location = raw_args[:pos]
        pos2 = raw_args.find(" ", pos + 1)
        if pos2 < 0:
            raise ValueError("Invalid args")
        factoid = raw_args[pos + 1:pos2]
        pos3 = raw_args.find(" ", pos2 + 1)
        info = raw_args[pos2 + 1:]
        if info == "":
            raise ValueError("Invalid args")
        return location, factoid, info

    def valid_location(self, location, source=None):
        """
        Checks if a given location is one of channel, protocol or global, and
        if it's a channel request, that it's in a channel.
        """
        location = location.lower()
        result = location in (self.CHANNEL, self.PROTOCOL, self.GLOBAL)
        if not result:
            raise InvalidLocationError("'%s' is not a valid location" %
                                       location)
        if source is not None:
            if location == self.CHANNEL and not isinstance(source, Channel):
                raise InvalidMethodError("'channel' location can only be used "
                                         "inside a channel")
        return True

    # endregion

    # region API functions to access factoids

    def _add_factoid_interaction(self, txn, factoid_key, location, factoid,
                                 info):
        """
        Appends a factoid to an existing one if there, otherwise creates it.
        :return: True if already exists, otherwise False
        """
        txn.execute("SELECT * FROM factoids WHERE "
                    "factoid_key = ? AND location = ?",
                    (factoid_key, location))
        results = txn.fetchall()
        if len(results) == 0:
            # Factoid doesn't exist yet, create it
            txn.execute("INSERT INTO factoids VALUES(?, ?, ?, ?)",
                        (
                            factoid_key,
                            location,
                            factoid,
                            info
                        ))
            return False
        else:
            # Factoid already exists, append
            txn.execute("INSERT INTO factoids VALUES(?, ?, ?, ?)",
                        (
                            results[0][0],
                            results[0][1],
                            results[0][2],
                            results[0][3] + "\n" + info
                        ))
            return True

    def _delete_factoid_interaction(self, txn, factoid_key, location):
        """
        Deletes a factoid if it exists, otherwise raises MissingFactoidError
        """
        txn.execute("DELETE FROM factoids WHERE "
                    "factoid_key = ? AND location = ?",
                    (factoid_key, location))
        if txn.rowcount == 0:
            raise MissingFactoidError("Factoid '%s' does not exist" %
                                      factoid_key)

    def _get_factoid_interaction(self, txn, factoid_key, location):
        """
        Deletes a factoid if it exists, otherwise raises MissingFactoidError
        :return: (factoid_name, [entry, entry, ...])
        """
        if location is None:
            txn.execute("SELECT location, factoid_name, info FROM factoids "
                        "WHERE factoid_key = ?",
                        (factoid_key,))
        else:
            txn.execute("SELECT location, factoid_name, info FROM factoids "
                        "WHERE factoid_key = ? AND location = ?",
                        (factoid_key, location))
        results = txn.fetchall()
        if len(results) == 0:
            raise MissingFactoidError("Factoid '%s' does not exist" %
                                      factoid_key)
        else:
            for loc in (self.CHANNEL, self.PROTOCOL, self.GLOBAL):
                for row in results:
                    if row[0] == loc:
                        return (row[1], row[2].split("\n"))
            # We shouldn't get here unless something else messes with the db
            self.logger.error("Unexpected location(s) in database for factoid "
                              "'%s'" % factoid_key)
            raise MissingFactoidError("Could not find a matchin factoid with "
                                      "a valid location")

    def add_factoid(self, caller, source, protocol, location, factoid, info):
        location = location.lower()
        factoid_key = factoid.lower()
        try:
            valid = location is None or self.valid_location(location, source)
        except Exception as ex:
            return defer.fail(ex)
        if not self.__check_perm(self.PERM_ADD % location,
                                 caller,
                                 source,
                                 protocol):
            return defer.fail(
                NoPermissionError("User does not have required permission"))
        with self.db as db:
            return db.runInteraction(self._add_factoid_interaction,
                                     factoid_key,
                                     location,
                                     factoid,
                                     info)

    def set_factoid(self, caller, source, protocol, location, factoid, info):
        location = location.lower()
        factoid_key = factoid.lower()
        try:
            valid = location is None or self.valid_location(location, source)
        except Exception as ex:
            return defer.fail(ex)
        if not self.__check_perm(self.PERM_SET % location,
                                 caller,
                                 source,
                                 protocol):
            return defer.fail(
                NoPermissionError("User does not have required permission"))
        with self.db as db:
            return db.runQuery(
                # Fuck you PEP8
                "INSERT INTO factoids VALUES(?, ?, ?, ?)",
                (
                    factoid_key,
                    location,
                    factoid,
                    info
                ))

    def delete_factoid(self, caller, source, protocol, location, factoid):
        location = location.lower()
        factoid_key = factoid.lower()
        try:
            valid = location is None or self.valid_location(location, source)
        except Exception as ex:
            return defer.fail(ex)
        if not self.__check_perm(self.PERM_DEL % location,
                                 caller,
                                 source,
                                 protocol):
            return defer.fail(
                NoPermissionError("User does not have required permission"))
        with self.db as db:
            return db.runInteraction(self._delete_factoid_interaction,
                                     factoid_key,
                                     location)

    def get_factoid(self, caller, source, protocol, location, factoid):
        if location is not None:
            location = location.lower()
        factoid_key = factoid.lower()
        try:
            valid = location is None or self.valid_location(location, source)
        except Exception as ex:
            return defer.fail(ex)
        if not self.__check_perm(self.PERM_GET % location,
                                 caller,
                                 source,
                                 protocol):
            return defer.fail(
                NoPermissionError("User does not have required permission"))
        with self.db as db:
            return db.runInteraction(self._get_factoid_interaction,
                                     factoid_key,
                                     location)

    # endregion

    # region Command handlers for interacting with factoids

    def _factoid_command_fail(self, caller, failure):
        """
        :type failure: twisted.python.failure.Failure
        """
        if failure.check(InvalidLocationError):
            caller.respond("Invalid location given - possible locations are: "
                           "channel, protocol, global")
        elif failure.check(InvalidMethodError):
            caller.respond("You must do that in a channel")
        elif failure.check(NoPermissionError):
            caller.respond("You don't have permission to do that")
        elif failure.check(MissingFactoidError):
            caller.respond("That factoid doesn't exist")
        else:
            # TODO: We should probably handle this
            failure.raiseException()

    def _factoid_get_command_success(self, source, result):
        for line in result[1]:
            source.respond("(%s) %s" % (result[0], line))

    def factoid_add_command(self, protocol, caller, source, command, raw_args,
                            parsed_args):
        try:
            location, factoid, info = self._parse_args(raw_args)
        except:
            caller.respond("Usage: %s <location> <factoid> <info>" % command)
            return
        d = self.add_factoid(caller, source, protocol, location, factoid, info)
        d.addCallbacks(
            lambda r: caller.respond("Factoid added"),
            lambda f: self._factoid_command_fail(caller, f)
        )

    def factoid_set_command(self, protocol, caller, source, command, raw_args,
                            parsed_args):
        try:
            location, factoid, info = self._parse_args(raw_args)
        except Exception as ex:
            caller.respond("Usage: %s <location> <factoid> <info>" % command)
            return
        d = self.set_factoid(caller, source, protocol, location, factoid, info)
        d.addCallbacks(
            lambda r: caller.respond("Factoid set"),
            lambda f: self._factoid_command_fail(caller, f)
        )

    def factoid_delete_command(self, protocol, caller, source, command,
                               raw_args, parsed_args):
        args = raw_args.split()  # Quick fix for new command handler signature
        if len(args) != 2:
            caller.respond("Usage: %s <location> <factoid>" % command)
            return
        location = args[0]
        factoid = args[1]
        d = self.delete_factoid(caller, source, protocol, location, factoid)
        d.addCallbacks(
            lambda r: caller.respond("Factoid deleted"),
            lambda f: self._factoid_command_fail(caller, f)
        )

    def factoid_get_command(self, protocol, caller, source, command, raw_args,
                            parsed_args):
        args = raw_args.split()  # Quick fix for new command handler signature
        if len(args) == 1:
            factoid = args[0]
            location = None
        elif len(args) == 2:
            location = args[0]
            factoid = args[1]
        else:
            caller.respond("Usage: %s [location] <factoid>" % command)
            return

        d = self.get_factoid(caller, source, protocol, location, factoid)
        d.addCallbacks(
            lambda r: self._factoid_get_command_success(source, r),
            lambda f: self._factoid_command_fail(caller, f)
        )

    # endregion

    def _print_query(self, result):
        from pprint import pprint
        pprint(result)

    def message_handler(self, event):
        """
        Handle ??-style factoid "commands"
        :type event: MessageReceived
        """
        handlers = {
            "??": self._message_handler_get,
            "??+": self._message_handler_add,
            "??~": self._message_handler_set,
            "??-": self._message_handler_delete
        }
        msg = event.message
        command = None
        factoid = ""
        args = ""
        pos = msg.find(" ")
        if pos < 0:
            command = msg
        else:
            command = msg[:pos]
            pos2 = msg.find(" ", pos + 1)
            if pos2 < 0:
                factoid = msg[pos + 1:].strip()
            else:
                factoid = msg[pos + 1:pos2].strip()
                args = msg[pos2 + 1:].strip()
        if command in handlers:
            handlers[command](command, factoid, args, event)

    def _message_handler_get(self, command, factoid, args, event):
        """
        Handle ?? factoid "command"
        :type event: MessageReceived
        """
        if not factoid:
            event.source.respond("Usage: ?? <factoid>")
            return
        d = self.get_factoid(event.source,
                             event.target,
                             event.source,
                             None,
                             factoid)
        d.addCallbacks(
            lambda r: self._factoid_get_command_success(event.target, r),
            lambda f: self._factoid_command_fail(event.source, f)
        )

    def _message_handler_add(self, command, factoid, args, event):
        """
        Handle ??+ factoid "command"
        :type event: MessageReceived
        """
        if not factoid or not args:
            event.source.respond("Usage: ??+ <factoid> <info>")
            return
        d = self.add_factoid(event.source,
                             event.target,
                             event.caller,
                             self.CHANNEL,
                             factoid,
                             args)
        d.addCallbacks(
            lambda r: event.source.respond("Factoid added"),
            lambda f: self._factoid_command_fail(event.source, f)
        )

    def _message_handler_set(self, command, factoid, args, event):
        """
        Handle ??~ factoid "command"
        :type event: MessageReceived
        """
        if not factoid or not args:
            event.source.respond("Usage: ??~ <factoid> <info>")
            return
        d = self.set_factoid(event.source,
                             event.target,
                             event.caller,
                             self.CHANNEL,
                             factoid,
                             args)
        d.addCallbacks(
            lambda r: event.source.respond("Factoid set"),
            lambda f: self._factoid_command_fail(event.source, f)
        )

    def _message_handler_delete(self, command, factoid, args, event):
        """
        Handle ??- factoid "command"
        :type event: MessageReceived
        """
        if factoid is None:
            event.source.respond("Usage: ??- <factoid>")
            return
        d = self.delete_factoid(event.source,
                                event.target,
                                event.caller,
                                self.CHANNEL,
                                factoid)
        d.addCallbacks(
            lambda r: event.source.respond("Factoid deleted"),
            lambda f: self._factoid_command_fail(event.source, f)
        )


class FactoidsError(Exception):
    pass


class InvalidLocationError(FactoidsError):
    pass


class InvalidMethodError(FactoidsError):
    pass


class NoPermissionError(FactoidsError):
    pass


class MissingFactoidError(FactoidsError):
    pass
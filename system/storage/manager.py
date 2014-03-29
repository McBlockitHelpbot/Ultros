__author__ = 'Gareth Coles'

from system.storage.exceptions import OtherOwnershipError, \
    UnknownStorageTypeError
from system.singleton import Singleton

import system.storage.files as files


class StorageManager(object):
    """
    Centralised data and configuration storage and access.
    """

    __metaclass__ = Singleton

    conf_path = ""
    data_path = ""

    config_files = {}
    data_files = {}

    def __init__(self, conf_path="config/", data_path="data/"):
        self.conf_path = conf_path
        self.data_path = data_path

    def get_file(self, obj, storage_type, file_format, path, *args, **kwargs):
        if ".." in path:
            path = path.replace("..", ".")

        if storage_type == "data":
            if path in self.data_files:
                if not self.data_files[path].is_owner(obj):
                    raise OtherOwnershipError("Data file %s is owned by "
                                              "another object." % path)
                return self.data_files[path].get()
            storage_file = files.DataFile(file_format, path, self.data_path,
                                          self.__class__, *args, **kwargs)
            storage_file.set_owner(self, obj)
            storage_file.make_ready(self)
            storage_file.load()

            self.data_files[path] = storage_file

            return storage_file.get()

        elif storage_type == "config":
            if path in self.config_files:
                if not self.config_files[path].is_owner(obj):
                    raise OtherOwnershipError("Data file %s is owned by "
                                              "another object." % path)
                return self.config_files[path].get()
            storage_file = files.ConfigFile(file_format, path, self.conf_path,
                                            self.__class__, *args, **kwargs)
            storage_file.set_owner(self, obj)
            storage_file.make_ready(self)
            storage_file.load()

            self.config_files[path] = storage_file

            return storage_file.get()

        else:
            raise UnknownStorageTypeError("Unknown storage type: %s"
                                          % storage_type)

    def release_file(self, obj, storage_type, path):
        if ".." in path:
            path = path.replace("..", ".")

        if storage_type == "data":
            if path in self.data_files:
                if not self.data_files[path].is_owner(obj):
                    raise OtherOwnershipError("Data file %s is owned by "
                                              "another object." % path)
                self.data_files[path].release()
                del self.data_files[path]
                return True
            return False

        elif storage_type == "config":
            if path in self.config_files:
                if not self.config_files[path].is_owner(obj):
                    raise OtherOwnershipError("Data file %s is owned by "
                                              "another object." % path)
                self.config_files[path].release()
                del self.config_files[path]
                return True
            return False

        else:
            raise UnknownStorageTypeError("Unknown storage type: %s"
                                          % storage_type)

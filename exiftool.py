# -*- coding: utf-8 -*-
# PyExifTool <http://github.com/smarnach/pyexiftool>
# Copyright 2012 Sven Marnach

# This file is part of PyExifTool.
#
# PyExifTool is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PyExifTool is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PyExifTool.  If not, see <http://www.gnu.org/licenses/>.

"""
PyExifTool is a Python library to communicate with an instance of Phil
Harvey's excellent ExifTool_ command-line application.  The library
provides the class :py:class:`ExifTool` that runs the command-line
tool in batch mode and features methods to send commands to that
program, including methods to extract meta-information from one or
more image files.  Since ``exiftool`` is run in batch mode, only a
single instance needs to be launched and can be reused for many
queries.  This is much more efficient than launching a separate
process for every single query.

.. _ExifTool: http://www.sno.phy.queensu.ca/~phil/exiftool/

The source code can be checked out from the github repository with

::

    git clone git://github.com/smarnach/pyexiftool.git

Alternatively, you can download a tarball_.  There haven't been any
releases yet.

.. _tarball: https://github.com/smarnach/pyexiftool/tarball/master

PyExifTool is licenced under GNU GPL version 3 or later.

Example usage::

    import exiftool

    for d in exiftool.metadata("a.jpg", "b.png", "c.tif"):
        print("{:20.20} {:20.20}".format(d["SourceFile"],
                                         d["EXIF:DateTimeOriginal"]))
    
    files = ["a.jpg", "b.png", "c.tif"]
    with exiftool.batch() as exif_batch:
        metadata = exif_batch.metadata(*files)
    for d in metadata:
        print("{:20.20} {:20.20}".format(d["SourceFile"],
                                         d["EXIF:DateTimeOriginal"]))
"""

from __future__ import unicode_literals

import sys
import subprocess
import os
import json
import warnings
import codecs
import logging
import itertools
import re

try:        # Py3k compatibility
    basestring
except NameError:
    basestring = (bytes, str)

executable = "exiftool"
"""The name of the executable to run.

If the executable is not located in one of the paths listed in the
``PATH`` environment variable, the full path should be given here.
"""

# Sentinel indicating the end of the output of a sequence of commands.
# The standard value should be fine.
sentinel = b"{ready}"

# The block size when reading from exiftool.  The standard value
# should be fine, though other values might give better performance in
# some cases.
block_size = 4096

# This code has been adapted from Lib/os.py in the Python source tree
# (sha1 265e36e277f3)
def _fscodec():
    encoding = sys.getfilesystemencoding()
    errors = "strict"
    if encoding != "mbcs":
        try:
            codecs.lookup_error("surrogateescape")
        except LookupError:
            pass
        else:
            errors = "surrogateescape"

    def fsencode(filename):
        """
        Encode filename to the filesystem encoding with 'surrogateescape' error
        handler, return bytes unchanged. On Windows, use 'strict' error handler if
        the file system encoding is 'mbcs' (which is the default encoding).
        """
        if isinstance(filename, bytes):
            return filename
        else:
            return filename.encode(encoding, errors)

    return fsencode

fsencode = _fscodec()
del _fscodec

class ExifTool(object):
    """Run the `exiftool` command-line tool and communicate to it.

    You can pass the file name of the ``exiftool`` executable as an
    argument to the constructor.  The default value ``exiftool`` will
    only work if the executable is in your ``PATH``.

    By default every call is made with a separate subprocess.

    Methods can be run in batch mode in the same process by calling
    :py:meth:`start()`, which will launch the subprocess with -stay_open.  
    To avoid leaving the subprocess running, make sure to call
    :py:meth:`terminate()` method when finished with your calls.
    This method will also be implicitly called when the instance is
    garbage collected, but there are circumstance when this won't ever
    happen, so you should not rely on the implicit process
    termination.  Subprocesses won't be automatically terminated if
    the parent process exits, so a leaked subprocess will stay around
    until manually killed.

    A convenient way to make sure that the subprocess is terminated is
    to use the :py:class:`ExifTool` instance as a context manager::

        with ExifTool() as et:
            ...

    .. warning:: Note that there is no error handling.  Nonsensical
       options will be silently ignored by exiftool, so there's not
       much that can be done in that regard.  You should avoid passing
       non-existent files to any of the methods, since this will lead
       to undefied behaviour.

    .. py:attribute:: running

       A Boolean value indicating whether this instance is currently
       associated with a running subprocess.
    """

    def __init__(self, executable_=None, fast=True):
        if executable_ is None:
            self.executable = executable
        else:
            self.executable = executable_
        self.fast = fast
        self.running = False

    def start(self):
        """Start an ``exiftool`` process in batch mode for this instance.

        This method will issue a ``UserWarning`` if the subprocess is
        already running.  The process is started with the ``-G`` and
        ``-n`` as common arguments, which are automatically included
        in every command you run with :py:meth:`execute()`.
        """
        if self.running:
            warnings.warn("ExifTool already running; doing nothing.")
            return
        with open(os.devnull, "w") as devnull:
            params = ["-stay_open", "True",  "-@", "-",
                 "-common_args", "-G", "-n"]
            if self.fast:
                params = ["-fast2"] + params
            self._process = subprocess.Popen(
                [self.executable] + params,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=devnull)
        self.running = True
        #self.groups = ''.join(et.execute(b'-listg').split('\n')[1:-1]).split()
        #self.tag_pattern =  r'(%s):\w+' % '|'.join(groups)

    def terminate(self):
        """Terminate the ``exiftool`` process of this instance.

        If the subprocess isn't running, this method will do nothing.
        """
        if not self.running:
            return
        self._process.stdin.write(b"-stay_open\nFalse\n")
        self._process.stdin.flush()
        self._process.communicate()
        del self._process
        self.running = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()

    def __del__(self):
        self.terminate()

    def _execute(self, *params):
        if not self.running:
            raise ValueError("ExifTool instance not running.")
        logging.debug("Executing '%s'" % b"\n".join(params + (b"-execute\n",)))
        self._process.stdin.write(b"\n".join(params + (b"-execute\n",)))
        self._process.stdin.flush()
        output = b""
        fd = self._process.stdout.fileno()
        while not output[-32:].strip().endswith(sentinel):
            output += os.read(fd, block_size)
        result = output.strip()[:-len(sentinel)]
        logging.debug("Result: '%s'" % result.decode('utf-8'))
        return result

    def execute(self, *params):
        """Execute the given batch of parameters with ``exiftool``.

        This method accepts any number of parameters and sends them to
        the attached ``exiftool`` process.  If there is no attached 
        ``exiftool`` process then this execution will create one for the 
        life time of the execution of this method.  The final
        ``-execute`` necessary to actually run the batch is appended
        automatically; see the documentation of :py:meth:`start()` for
        the common options.  The ``exiftool`` output is read up to the
        end-of-output sentinel and returned as a raw ``bytes`` object,
        excluding the sentinel.

        The parameters must also be raw ``bytes``, in whatever
        encoding exiftool accepts.  For filenames, this should be the
        system's filesystem encoding.

        .. note:: This is considered a low-level method, and should
           rarely be needed by application developers.
        """
        if self.running:
            return self._execute(*params)
        else:
            with self as et:
                return et._execute(*params)

    def execute_json(self, *params):
        """Execute the given batch of parameters and parse the JSON output.

        This method is similar to :py:meth:`execute()`.  It
        automatically adds the parameter ``-j`` to request JSON output
        from ``exiftool`` and parses the output.  The return value is
        a list of dictionaries, mapping tag names to the corresponding
        values.  All keys are Unicode strings with the tag names
        including the ExifTool group name in the format <group>:<tag>.
        The values can have multiple types.  All strings occurring as
        values will be Unicode strings.  Each dictionary contains the
        name of the file it corresponds to in the key ``"SourceFile"``.

        The parameters to this function must be either raw strings
        (type ``str`` in Python 2.x, type ``bytes`` in Python 3.x) or
        Unicode strings (type ``unicode`` in Python 2.x, type ``str``
        in Python 3.x).  Unicode strings will be encoded using
        system's filesystem encoding.  This behaviour means you can
        pass in filenames according to the convention of the
        respective Python version – as raw strings in Python 2.x and
        as Unicode strings in Python 3.x.
        """
        params = map(fsencode, params)
        return json.loads(self.execute(b"-j", b"-struct", *params).decode("utf-8"))


    def metadata(self, *file_paths):
        '''Returns the metadata for files.

        This returns a FileMetadata if only one file is specified. If a dir or file pattern is 
        specified then this returns a MultiFileMetadata.
        '''
        if len(file_paths) == 1 and os.path.isfile(file_paths[0]):
            file_path = file_paths[0]
            exif_values = next(x for x in self.execute_json(file_path))
            return FileMetadata(exif_values, exif_tool=self)
        else:
            return MultiFileMetadata(file_paths, exif_tool=self)

    def write_metadata(self, *file_paths, **tags):
        '''Bulk update of tags for files'''
        if tags:
            params = []
            for tag, value in tags.items():
                if isinstance(value, CopiedValue):
                    params.append(u'-%s<%s' % (tag, value.expression))
                else:
                    params.append(u'-%s=%s' % (tag, value))
            params.append(b'-r')
            params += file_paths
            result = self.execute(*map(fsencode, params))
            if not result or 'errors' in result:
                # Fail fast if errors are detected
                raise Exception("Unable to update files %s: %s" % (', '.join('"{0}"'.format(fp) for fp in file_paths), result))
            elif 'unchanged' in results:
                logging.warning('Some files were unchanged: %s' % results)

class CopiedValue(object):
    '''Used to indicate that this value is constructed by copying other tags values

    For safety all tag names should be written as ${TAG}. 
    See the '-tagsFromFile' and '-p' options for details on acceptable formats.
    '''

    # TODO date format
    # TODO other formats?

    def __init__(self, expression):
        self.expression = expression

    def render(self, exif_tool, *file_paths):
        # TODO render using "-p"
        pass


class MultiFileTagValue(object):
    '''Used to allow for operator overloading on += and -= assignment.'''

    def __init__(self, container, tag):
        self.container = container
        self.tag = tag

    def __iter__(self):
        '''Returns iterable of all values of a given field for every FileMetadata

        This is useful if you intend to aggregate the values in some way.
        '''
        exif_tool = self.container.exif_tool
        edit = self.container._edits.get(self.tag)
        if isinstance(edit, CopiedValue):
            params = ['-p', self.value.expression, '-r'] + self.container.file_paths
            params = map(fsencode, params)
            for render in exif_tool.execute(*params).split('\n'):
                yield render
        else:
            for exif_values in exif_tool.execute_json('-%s' % self.tag, '-r', *self.container.file_paths):
                yield edit if self.tag in self.container._edits else exif_values.get(self.tag, None)

    def __iadd__(self, other):
        self.container._edits['%s+' % self.tag] = other
        if self.container.autowrite:
            self.container.write()
        return self

    def __isub__(self, other):
        self.container._edits['%s-' % self.tag] = other
        if self.container.autowrite:
            self.container.write()
        return self

length_pattern = re.compile('\d+(?= files failed condition)')

class MultiFileMetadata(object):
    '''Represents the metadata for multiple files.

    You can easily aggregate and batch modify a set files using standard dict like operations.
    '''
    # iterable or FileMetadata
    # dictionary like

    def __init__(self, file_paths, exif_tool, autowrite=True):
        self.file_paths = file_paths
        self.exif_tool = exif_tool
        self.autowrite = autowrite
        self._edits = {}

    def keys(self):
        return {key for key in itertools.chain.from_iterable(self)}

    def values(self):
        # Not very useful without key names, but here for the sake of completion
        for metadata in self:
            for value in metadata.values():
                yield value

    def __len__(self):
        result = self.exif_tool.execute(b'-if', b'false', b'-r', *map(fsencode, self.file_paths))
        return int(length_pattern.findall(result)[0])

    def __iter__(self):
        '''Returns iterable of individual FileMetadata objects'''
        for exif_values in self.exif_tool.execute_json(b'-r', *self.file_paths):
            yield FileMetadata(exif_values, self.exif_tool)

    def __contains__(self, item):
        return item in self.keys()

    def __getitem__(self, key):
        # TODO account for edits and render appropriately
        return MultiFileTagValue(self, key)

    def __setitem__(self, key, value):
        # TODO Account for MultiFileMetadataItem in case of += or assigning from another field
        self._edits[key] = value
        if self.autowrite:
            self.write()

    def __delitem__(self, key):
        self._edits[key] = None
        if self.autowrite:
            self.write()

    def __repr__(self):
        return 'MultiFileMetadata for %s' % ', '.join('"{0}"'.format(fp) for fp in self.file_paths)

    def write(self):
        '''Persist changes to files on disk.'''
        if self._edits:
            self.exif_tool.write_metadata(*self.file_paths, **self._edits)
            self._edits = {}

class FileMetadata(dict):

    def __init__(self, values, exif_tool, autowrite=True):
        super(FileMetadata, self).__init__(values)
        self.exif_tool = exif_tool
        self.file_path = values['SourceFile']
        self._edits = {}

    def __getitem__(self, key):
        # TODO check for edits and render appropriately
        return super(FileMetadata, self).__getitem__(key)

    def __setitem__(self, key, value):
        super(FileMetadata, self).__setitem__(key, value)
        self._edits[key] = value
        if self.autowrite:
            self.write()

    def __delitem__(self, key):
        super(FileMetadata, self).__delitem__(key)
        self._edits[key] =  None
        if self.autowrite:
            self.write()

    def write(self):
        '''Store changes to disk'''
        if self._edits:
            self.exif_tool.write_metadata(self.file_path, **self._edits)
            self._edits.clear()
            self.clear()
            self.update(self.exif_tool.metadata(self.file_path))


def metadata(*file_paths):
    '''Convenience method that returns the metadata for files.

    This returns a FileMetadata if only one file is specified. If a dir or file pattern is 
    specified then this returns a MultiFileMetadata.
    '''
    with ExifTool() as et:
        return et.metadata(*file_paths)

__all__ = ("metadata", "ExifTool", "CopiedValue")
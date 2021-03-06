# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Test for Shared class."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gc
import threading
import time

from tensorflow_transform.beam import shared
import unittest


class Count(object):

  def __init__(self):
    self._lock = threading.Lock()
    self._total = 0
    self._active = 0

  def add_ref(self):
    with self._lock:
      self._total += 1
      self._active += 1

  def release_ref(self):
    with self._lock:
      self._active -= 1

  def get_active(self):
    with self._lock:
      return self._active

  def get_total(self):
    with self._lock:
      return self._total


class Marker(object):

  def __init__(self, count):
    self._count = count
    self._count.add_ref()

  def __del__(self):
    self._count.release_ref()


class NamedObject(object):

  def __init__(self, name):
    self._name = name

  def get_name(self):
    return self._name


class Sequence(object):

  def __init__(self):
    self._sequence = 0

  def make_acquire_fn(self):
    # Every time acquire_fn is called, increases the sequence number and returns
    # a NamedObject with that sequenece number.
    def acquire_fn():
      self._sequence += 1
      return NamedObject('sequence%d' % self._sequence)

    return acquire_fn


class SharedTest(unittest.TestCase):

  def testKeepalive(self):
    count = Count()
    shared_handle = shared.Shared()
    other_shared_handle = shared.Shared()

    def dummy_acquire_fn():
      return None

    def acquire_fn():
      return Marker(count)

    p1 = shared_handle.acquire(acquire_fn)
    self.assertEquals(1, count.get_total())
    self.assertEquals(1, count.get_active())
    del p1
    gc.collect()
    # Won't be garbage collected, because of the keep-alive
    self.assertEquals(1, count.get_active())

    # Reacquire.
    p2 = shared_handle.acquire(acquire_fn)
    self.assertEquals(1, count.get_total())  # No reinitialisation.
    self.assertEquals(1, count.get_active())

    # Get rid of the keepalive
    other_shared_handle.acquire(dummy_acquire_fn)
    del p2
    gc.collect()
    self.assertEquals(0, count.get_active())

  def testMultiple(self):
    count = Count()
    shared_handle = shared.Shared()
    other_shared_handle = shared.Shared()

    def dummy_acquire_fn():
      return None

    def acquire_fn():
      return Marker(count)

    p = shared_handle.acquire(acquire_fn)
    other_shared_handle.acquire(dummy_acquire_fn)  # Get rid of the keepalive
    self.assertEquals(1, count.get_total())
    self.assertEquals(1, count.get_active())
    del p
    gc.collect()
    self.assertEquals(0, count.get_active())
    # Shared value should be garbage collected.

    # Acquiring multiple times only results in one initialisation
    p1 = shared_handle.acquire(acquire_fn)
    # Since shared value was released, expect a reinitialisation.
    self.assertEquals(2, count.get_total())
    self.assertEquals(1, count.get_active())
    p2 = shared_handle.acquire(acquire_fn)
    self.assertEquals(2, count.get_total())
    self.assertEquals(1, count.get_active())

    other_shared_handle.acquire(dummy_acquire_fn)  # Get rid of the keepalive

    # Check that shared object isn't destroyed if there's still a reference to
    # it.
    del p2
    gc.collect()
    self.assertEquals(1, count.get_active())

    del p1
    gc.collect()
    self.assertEquals(0, count.get_active())

  def testConcurrentCallsDeduped(self):
    # Test that only one among many calls to acquire will actually run the
    # initialisation function.

    count = Count()
    shared_handle = shared.Shared()
    other_shared_handle = shared.Shared()

    refs = []
    ref_lock = threading.Lock()

    def dummy_acquire_fn():
      return None

    def acquire_fn():
      time.sleep(1)
      return Marker(count)

    def thread_fn():
      p = shared_handle.acquire(acquire_fn)
      with ref_lock:
        refs.append(p)

    threads = []
    for _ in range(100):
      t = threading.Thread(target=thread_fn)
      threads.append(t)
      t.start()

    for t in threads:
      t.join()

    self.assertEquals(1, count.get_total())
    self.assertEquals(1, count.get_active())

    other_shared_handle.acquire(dummy_acquire_fn)  # Get rid of the keepalive

    with ref_lock:
      del refs[:]
    gc.collect()

    self.assertEquals(0, count.get_active())

  def testDifferentObjects(self):
    sequence = Sequence()

    def dummy_acquire_fn():
      return None

    first_handle = shared.Shared()
    second_handle = shared.Shared()
    dummy_handle = shared.Shared()

    f1 = first_handle.acquire(sequence.make_acquire_fn())
    s1 = second_handle.acquire(sequence.make_acquire_fn())

    self.assertEquals('sequence1', f1.get_name())
    self.assertEquals('sequence2', s1.get_name())

    f2 = first_handle.acquire(sequence.make_acquire_fn())
    s2 = second_handle.acquire(sequence.make_acquire_fn())

    # Check that the repeated acquisitions return the earlier objects
    self.assertEquals('sequence1', f2.get_name())
    self.assertEquals('sequence2', s2.get_name())

    # Release all references and force garbage-collection
    del f1
    del f2
    del s1
    del s2
    dummy_handle.acquire(dummy_acquire_fn)  # Get rid of the keepalive
    gc.collect()

    # Check that acquiring again after they're released gives new objects
    f3 = first_handle.acquire(sequence.make_acquire_fn())
    s3 = second_handle.acquire(sequence.make_acquire_fn())
    self.assertEquals('sequence3', f3.get_name())
    self.assertEquals('sequence4', s3.get_name())


if __name__ == '__main__':
  unittest.main()

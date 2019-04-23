#!/usr/bin/env python

#
# Copyright (c) 2018-2019 Intel Corporation
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.
#
"""
Rosbridge class:

Class that handle communication between CARLA and ROS
"""
import threading
import time

from carla_ros_bridge.actor import Actor
from carla_ros_bridge.sensor import Sensor

from carla_ros_bridge.actors.map import Map
from carla_ros_bridge.actors.spectator import Spectator
from carla_ros_bridge.actors.traffic import Traffic, TrafficLight
from carla_ros_bridge.actors.vehicle import Vehicle
from carla_ros_bridge.actors.lidar import Lidar
from carla_ros_bridge.actors.gnss import Gnss
from carla_ros_bridge.actors.ego_vehicle import EgoVehicle
from carla_ros_bridge.actors.collision_sensor import CollisionSensor
from carla_ros_bridge.actors.lane_invasion_sensor import LaneInvasionSensor
from carla_ros_bridge.actors.camera import Camera, RgbCamera, DepthCamera, SemanticSegmentationCamera
from carla_ros_bridge.actors.object_sensor import ObjectSensor

class CarlaRosBridge(object):

    """
    Carla Ros bridge
    """

    def __init__(self, carla_world, binding):
        """
        Constructor

        :param carla_world: carla world object
        :type carla_world: carla.World
        :param binding: binding
        """
        self.actors = {}
        self.carla_world = carla_world

        self.binding = binding
        self.timestamp_last_run = 0.0

        # register callback to create/delete actors
        self.update_child_actors_lock = threading.Lock()
        self.carla_world.on_tick(self._carla_update_child_actors)

        # register callback to update actors
        self.update_lock = threading.Lock()
        self.carla_world.on_tick(self._carla_time_tick)

        self.pseudo_actors = []

        # add map
        self.pseudo_actors.append(Map(carla_world=self.carla_world,
                                      topic_prefix='/map',
                                      binding=binding))

        # add overall object sensor
        self.pseudo_actors.append(ObjectSensor(parent=None,
                                               topic_prefix='objects',
                                               binding=binding,
                                               actor_list=self.actors,
                                               filtered_id=None))

    def destroy(self):
        """
        Function (virtual) to destroy this object.

        Lock the update mutex.
        Remove all publisher.
        Finally forward call to super class.

        :return:
        """
        self.update_child_actors_lock.acquire()
        self.update_lock.acquire()
        self.get_binding().signal_shutdown("")
        self.get_binding().loginfo("Exiting Bridge")

    def _carla_time_tick(self, carla_timestamp):
        """
        Private callback registered at carla.World.on_tick()
        to trigger cyclic updates.

        After successful locking the update mutex
        (only perform trylock to respect bridge processing time)
        the clock and the children are updated.
        Finally the messages collected to be published are sent out.

        :param carla_timestamp: the current carla time
        :type carla_timestamp: carla.Timestamp
        :return:
        """
        if not self.get_binding().is_shutdown():
            if self.update_lock.acquire(False):
                if self.timestamp_last_run < carla_timestamp.elapsed_seconds:
                    self.timestamp_last_run = carla_timestamp.elapsed_seconds
                    self.binding.update_clock(carla_timestamp)
                    self.update()
                    self.binding.send_msgs()
                self.update_lock.release()

    def _carla_update_child_actors(self, _):
        """
        Private callback registered at carla.World.on_tick()
        to trigger cyclic updates of the actors

        After successful locking the mutex
        (only perform trylock to respect bridge processing time)
        the existing actors are updated.

        :param carla_timestamp: the current carla time
        :type carla_timestamp: carla.Timestamp
        :return:
        """
        if not self.get_binding().is_shutdown():
            if self.update_child_actors_lock.acquire(False):
                self.update_actors()
                # actors are only created/deleted around once per second
                time.sleep(1)
                self.update_child_actors_lock.release()

    def update_actors(self):
        """
        update the available actors
        """
        carla_actors = self.carla_world.get_actors()
        # Add new actors
        for actor in carla_actors:
            if actor.id not in self.actors.keys():
                self.create_actor(actor)

        # create list of carla actors ids
        carla_actor_ids = []
        for actor in carla_actors:
            carla_actor_ids.append(actor.id)

        # remove non-existing actors
        for actor_id in self.actors.keys():
            id_to_delete = None
            if actor_id not in carla_actor_ids:
                id_to_delete = actor_id

            if id_to_delete:
                self.get_binding().logwarn("Remove Actor {}".format(id_to_delete))
                del self.actors[id_to_delete]

    def create_actor(self, carla_actor):
        """
        create an actor
        """
        parent = None
        if carla_actor.parent:
            if carla_actor.parent.id in self.actors:
                parent = self.actors[carla_actor.parent.id]
            else:
                parent = self.create_actor(carla_actor.parent)

        actor = None
        if carla_actor.type_id.startswith('traffic'):
            if carla_actor.type_id == "traffic.traffic_light":
                actor = TrafficLight(carla_actor, parent, self.binding)
            else:
                actor = Traffic(carla_actor, parent, self.binding)
        elif carla_actor.type_id.startswith("vehicle"):
            if carla_actor.attributes.get('role_name')\
                    in self.binding.get_parameters()['ego_vehicle']['role_name']:
                actor = EgoVehicle(carla_actor, parent, self.binding)
                with self.update_lock:
                    self.pseudo_actors.append(ObjectSensor(parent=actor,
                                                           topic_prefix='objects',
                                                           binding=self.binding,
                                                           actor_list=self.actors,
                                                           filtered_id=carla_actor.id))
            else:
                actor = Vehicle(carla_actor, parent, self.binding)
        elif carla_actor.type_id.startswith("sensor"):
            if carla_actor.type_id.startswith("sensor.camera"):
                if carla_actor.type_id.startswith("sensor.camera.rgb"):
                    actor = RgbCamera(carla_actor, parent, self.binding)
                elif carla_actor.type_id.startswith("sensor.camera.depth"):
                    actor = DepthCamera(carla_actor, parent, self.binding)
                elif carla_actor.type_id.startswith("sensor.camera.semantic_segmentation"):
                    actor = SemanticSegmentationCamera(carla_actor, parent, self.binding)
                else:
                    actor = Camera(carla_actor, parent, self.binding)
            if carla_actor.type_id.startswith("sensor.lidar"):
                actor = Lidar(carla_actor, parent, self.binding)
            if carla_actor.type_id.startswith("sensor.other.gnss"):
                actor = Gnss(carla_actor, parent, self.binding)
            if carla_actor.type_id.startswith("sensor.other.collision"):
                actor = CollisionSensor(carla_actor, parent, self.binding)
            if carla_actor.type_id.startswith("sensor.other.lane_invasion"):
                actor = LaneInvasionSensor(carla_actor, parent, self.binding)
            else:
                actor = Sensor(carla_actor, parent, self.binding)
        elif carla_actor.type_id.startswith("spectator"):
            actor = Spectator(carla_actor, parent, self.binding)
        else:
            actor = Actor(carla_actor, parent, self.binding)

        self.get_binding().logwarn("Created Actor-{}(id={}, parent_id={},"
                                   " type={}, prefix={}, attributes={}".format(
                                       actor.__class__.__name__, actor.get_id(),
                                       actor.get_parent_id(), carla_actor.type_id,
                                       actor.get_topic_prefix(), carla_actor.attributes))
        with self.update_lock:
            self.actors[carla_actor.id] = actor
        return actor

    def run(self):
        """
        Run the bridge functionality.

        Registers on shutdown callback and spins

        :return:
        """
        self.get_binding().on_shutdown(self.on_shutdown)
        self.get_binding().spin()

    def on_shutdown(self):
        """
        Function to be called on shutdown.

        This function is registered as shutdown handler.

        """
        self.get_binding().loginfo("Shutdown requested")
        self.destroy()

    def get_binding(self):
        """
        get the binding
        """
        return self.binding

    def update(self):
        """
        update all actors

        :return:
        """
        # update all pseudo actors
        for actor in self.pseudo_actors:
            actor.update()

        # updaate all carla actors
        for actor_id in self.actors.keys():
            try:
                self.actors[actor_id].update()
            except RuntimeError as e:
                self.get_binding().logwarn("Update actor {}({}) failed: {}".format(
                    self.actors[actor_id].__class__.__name__, actor_id, e))
                continue

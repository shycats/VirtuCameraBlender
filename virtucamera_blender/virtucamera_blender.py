# VirtuCameraBlender
# Copyright (c) 2021-2022 Pablo Javier Garcia Gonzalez.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THE SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# Python modules
import os
import sys
import math
import ctypes
import traceback

# Blender modules
import bpy
import bpy.utils.previews
import bgl
import mathutils

# VirtuCamera API
from .virtucamera import VCBase, VCServer

plugin_version = (1, 1, 1)

class VirtuCameraBlender(VCBase):
    # Constants
    TRANSFORM_CHANNELS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle")
    B_TO_V_ROTATION_MAT = mathutils.Matrix((
        (1, 0, 0, 0),
        (0, 0,-1, 0),
        (0, 1, 0, 0),
        (0, 0, 0, 1)
    ))
    V_TO_B_ROTATION_MAT = mathutils.Matrix((
        (1, 0, 0, 0),
        (0, 0, 1, 0),
        (0,-1, 0, 0),
        (0, 0, 0, 1)
    ))

    # Cached Viewport capture rect
    last_rect_data = None


    # -- Utility Functions ------------------------------------

    def camera_rect_changed(self, offset_value_x, offset_value_y, zoom_value, region_rect, camera_aspect_ratio):
        rect_data = (offset_value_x, offset_value_y, zoom_value, region_rect, camera_aspect_ratio)
        if self.last_rect_data != rect_data:
            self.last_rect_data = rect_data
            return True
        return False

    def view_zoom_factor(self, zoom_value):
        return ((zoom_value / 50 + math.sqrt(2)) / 2) ** 2

    def view_region_width_zoom_factor(self, zoom_factor, region_aspect_ratio, camera_aspect_ratio):
        return zoom_factor * min(camera_aspect_ratio, 1) / min(region_aspect_ratio, 1)

    def view_offset_factor(self, offset_value, zoom_factor):
        return offset_value * zoom_factor * -2

    def get_view_camera_rect(self):
        scene = bpy.context.scene
        render = scene.render
        context = scene.virtucamera.contexts['start']
        region = context['region']
        r3d = context['space_data'].region_3d

        zoom_value = r3d.view_camera_zoom
        offset_value_x = r3d.view_camera_offset[0]
        offset_value_y = r3d.view_camera_offset[1]
        region_rect = (region.x, region.y, region.width, region.height)
        camera_aspect_ratio = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)

        # Check if zoom, offset, aspect or region size/pos changes and return cached rect otherwise
        if self.camera_rect_changed(offset_value_x, offset_value_y, zoom_value, region_rect, camera_aspect_ratio):
            region_aspect_ratio = region.width / region.height

            zoom_factor = self.view_zoom_factor(zoom_value)

            width_factor = self.view_region_width_zoom_factor(zoom_factor, region_aspect_ratio, camera_aspect_ratio)
            width = int(region.width * width_factor)
            height = int(width / camera_aspect_ratio)

            offset_factor_x = self.view_offset_factor(offset_value_x, zoom_factor)
            offset_factor_y = self.view_offset_factor(offset_value_y, zoom_factor)
            x = int(region.x + (region.width - width) * 0.5 + region.width * offset_factor_x)
            y = int(region.y + (region.height - height) * 0.5 + region.height * offset_factor_y)

            # Clamp values to region rect
            width = min(width, region.width)
            height = min(height, region.height)
            x = min(max(x, region.x), region.x + region.width - width)
            y = min(max(y, region.y), region.y + region.height - height)

            self.last_rect = (x, y, width, height)

        return self.last_rect

    def init_capture_buffer(self, width, height):
        self.buffer = bgl.Buffer(bgl.GL_BYTE, [4, width * height])
        """
        Based on the C Struct defined in bgl.h for the Buffer object, extract the buffer pointer

        typedef struct _Buffer {
          PyObject_VAR_HEAD PyObject *parent;

          int type; /* GL_BYTE, GL_SHORT, GL_INT, GL_FLOAT */
          int ndimensions;
          int *dimensions;

          union {
            char *asbyte;
            short *asshort;
            int *asint;
            float *asfloat;
            double *asdouble;

            void *asvoid;
          } buf;
        } Buffer;
        """
        # Get the size of the Buffer Python object
        buffer_obj_size = sys.getsizeof(self.buffer)
        # Get the size of void * C-type
        buffer_pointer_size = ctypes.sizeof(ctypes.c_void_p)
        # Calculate the address to the pointer assuming that it's at the end of the C Struct
        buffer_pointer_addr = id(self.buffer) + buffer_obj_size - buffer_pointer_size
        # Get the actual pointer value as a Python Int
        self.buffer_pointer = (ctypes.c_void_p).from_address(buffer_pointer_addr).value

    def get_script_files(self):
        scripts_dir = bpy.context.scene.virtucamera.custom_scripts_dir
        if not os.path.isdir(scripts_dir):
            return []
        dir_files = os.listdir(scripts_dir)
        dir_files.sort()

        valid_files = []
        for file in dir_files:
            if file.endswith(".py"):
                filepath = os.path.join(scripts_dir, file)
                if os.path.isdir(filepath):
                    continue
                valid_files.append(filepath)

        return valid_files


    # SCENE STATE RELATED METHODS:
    # ---------------------------

    def get_playback_state(self, vcserver):
        """ Must Return the playback state of the scene as a tuple or list
        in the following order: (current_frame, range_start, range_end)
        * current_frame (float) - The current frame number.
        * range_start (float) - Animation range start frame number.
        * range_end (float) - Animation range end frame number.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.

        Returns
        -------
        tuple or list of 3 floats
            playback state as (current_frame, range_start, range_end)
        """

        current_frame = bpy.context.scene.frame_current
        range_start = bpy.context.scene.frame_start
        range_end = bpy.context.scene.frame_end
        return (current_frame, range_start, range_end)


    def get_playback_fps(self, vcserver):
        """ Must return a float value with the scene playback rate
        in Frames Per Second.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.

        Returns
        -------
        float
            scene playback rate in FPS.
        """

        return bpy.context.scene.render.fps


    def set_frame(self, vcserver, frame):
        """ Must set the current frame number on the scene

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        frame : float
            The current frame number.
        """

        bpy.context.scene.frame_current = int(frame)


    def set_playback_range(self, vcserver, start, end):
        """ Must set the animation frame range on the scene

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        start : float
            Animation range start frame number.
        end : float
            Animation range end frame number.
        """

        bpy.context.scene.frame_start = int(start)
        bpy.context.scene.frame_end = int(end)


    def start_playback(self, vcserver, forward):
        """ This method must start the playback of animation in the scene.
        Not used at the moment, but must be implemented just in case
        the app starts using it in the future. At the moment
        VCBase.set_frame() is called instead.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        forward : bool
            if True, play the animation forward, if False, play it backwards.
        """

        if not bpy.context.screen.is_animation_playing:
            # This operator acts like a toggle, so we need to first check if it's playing
            bpy.ops.screen.animation_play(reverse=(not forward), sync=True)


    def stop_playback(self, vcserver):
        """ This method must stop the playback of animation in the scene.
        Not used at the moment, but must be implemented just in case
        the app starts using it in the future.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        """

        bpy.ops.screen.animation_cancel(restore_frame=False)


    # CAMERA RELATED METHODS:
    # -----------------------

    def get_scene_cameras(self, vcserver):
        """ Must Return a list or tuple with the names of all the scene cameras.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.

        Returns
        -------
        tuple or list
            names of all the scene cameras.
        """

        scene_cameras = []
        for obj in bpy.data.objects:
            # Filter invisible cameras, as euler filter only works on visible cameras.
            if obj.type == "CAMERA" and obj.visible_get():
                scene_cameras.append(obj)
        camera_names = [camera.name for camera in scene_cameras]
        return camera_names


    def get_camera_exists(self, vcserver, camera_name):
        """ Must Return True if the specified camera exists in the scene,
        False otherwise.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to check for.

        Returns
        -------
        bool
            'True' if the camera 'camera_name' exists, 'False' otherwise.
        """

        # Filter invisible cameras, as euler filter only works on visible cameras.
        if camera_name in bpy.data.objects and bpy.data.objects[camera_name].visible_get():
            return True
        return False


    def get_camera_has_keys(self, vcserver, camera_name):
        """ Must Return whether the specified camera has animation keyframes
        in the transform or flocal length parameters, as a tuple or list,
        in the following order: (transform_has_keys, focal_length_has_keys)
        * transform_has_keys (bool) - True if the transform has keyframes.
        * focal_length_has_keys (bool) - True if the flen has keyframes.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to check for.

        Returns
        -------
        tuple or list of 2 bool
            whether the camera 'camera_name' has keys or not as
            (transform_has_keys, focal_length_has_keys)
        """

        camera = bpy.data.objects[camera_name]
        
        transform_has_keys = False
        if camera.animation_data and camera.animation_data.action:
            for fcu in camera.animation_data.action.fcurves:
                if fcu.data_path in self.TRANSFORM_CHANNELS:
                    transform_has_keys = True
                    break
        
        focal_length_has_keys = False
        if camera.data.animation_data and camera.data.animation_data.action:
            for fcu in camera.data.animation_data.action.fcurves:
                if fcu.data_path == "lens":
                    focal_length_has_keys = True
                    break

        return (transform_has_keys, focal_length_has_keys)


    def get_camera_focal_length(self, vcserver, camera_name):
        """ Must Return the focal length value of the specified camera.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to get the data from.

        Returns
        -------
        float
            focal length value of the camera 'camera_name'.
        """

        camera_data = bpy.data.objects[camera_name].data
        return camera_data.lens


    def get_camera_transform(self, vcserver, camera_name):
        """ Must return a tuple or list of 16 floats with the 4x4
        transform matrix of the specified camera.

        * The up axis must be Y+
        * The order must be:
            (rxx, rxy, rxz, 0,
            ryx, ryy, ryz, 0,
            rzx, rzy, rzz, 0,
            tx,  ty,  tz,  1)
            Being 'r' rotation and 't' translation,

        Is your responsability to rotate or transpose the matrix if needed,
        most 3D softwares offer fast APIs to do so.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to get the data from.

        Returns
        -------
        tuple or list of 16 float
            4x4 transform matrix as
            (rxx, rxy, rxz, 0, ryx, ryy, ryz, 0, rzx, rzy, rzz, 0 , tx, ty, tz, 1)
        """

        camera_matrix = bpy.data.objects[camera_name].matrix_local.transposed()
        # Blender is Z+ up, so we rotate the transform matrix
        camera_matrix @= self.B_TO_V_ROTATION_MAT
        camera_matrix_tuple = (
            *camera_matrix[0],
            *camera_matrix[1],
            *camera_matrix[2],
            *camera_matrix[3]
        )
        return camera_matrix_tuple


    def set_camera_focal_length(self, vcserver, camera_name, focal_length):
        """ Must set the focal length of the specified camera.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to set the focal length to.
        focal_length : float
            focal length value to be set on the camera 'camera_name'
        """

        camera_data = bpy.data.objects[camera_name].data
        camera_data.lens = focal_length


    def set_camera_transform(self, vcserver, camera_name, transform_matrix):
        """  Must set the transform of the specified camera.
        The transform matrix is provided as a tuple of 16 floats
        with a 4x4 transform matrix.

        * The up axis is Y+
        * The order is:
            (rxx, rxy, rxz, 0,
            ryx, ryy, ryz, 0,
            rzx, rzy, rzz, 0,
            tx,  ty,  tz,  1)
            Being 'r' rotation and 't' translation,

        Is your responsability to rotate or transpose the matrix if needed,
        most 3D softwares offer fast APIs to do so.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to set the transform to.
        transform_matrix : tuple of 16 floats
            transformation matrix to be set on the camera 'camera_name'
        """

        camera = bpy.data.objects[camera_name]
        matrix = mathutils.Matrix((
            transform_matrix[0:4],
            transform_matrix[4:8],
            transform_matrix[8:12],
            transform_matrix[12:16]
        ))
        # Blender is Z+ up, so we rotate the transform matrix
        matrix @= self.V_TO_B_ROTATION_MAT
        matrix.transpose()
        camera.matrix_local = matrix


    def set_camera_flen_keys(self, vcserver, camera_name, keyframes, focal_length_values):
        """ Must set keyframes on the focal length of the specified camera.
        The frame numbers are provided as a tuple of floats and
        the focal length values are provided as a tuple of floats
        with a focal length value for every keyframe.

        The first element of the 'keyframes' tuple corresponds to the first
        element of the 'focal_length_values' tuple, the second to the second,
        and so on.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to set the keyframes to.
        keyframes : tuple of floats
            Frame numbers to create the keyframes on.
        focal_length_values : tuple of floats
            focal length values to be set as keyframes on the camera 'camera_name'
        """

        camera_data = bpy.data.objects[camera_name].data
        for keyframe, focal_length in zip(keyframes, focal_length_values):
            camera_data.lens = focal_length
            camera_data.keyframe_insert('lens', frame=keyframe)


    def set_camera_transform_keys(self, vcserver, camera_name, keyframes, transform_matrix_values):
        """ Must set keyframes on the transform of the specified camera.
        The frame numbers are provided as a tuple of floats and
        the transform matrixes are provided as a tuple of tuples of 16 floats
        with 4x4 transform matrixes, with a matrix for every keyframe.

        The first element of the 'keyframes' tuple corresponds to the first
        element of the 'transform_matrix_values' tuple, the second to the second,
        and so on.

        * The up axis is Y+
        * The order is:
            (rxx, rxy, rxz, 0,
            ryx, ryy, ryz, 0,
            rzx, rzy, rzz, 0,
            tx,  ty,  tz,  1)
            Being 'r' rotation and 't' translation,

        Is your responsability to rotate or transpose the matrixes if needed,
        most 3D softwares offer fast APIs to do so.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to set the keyframes to.
        keyframes : tuple of floats
            Frame numbers to create the keyframes on.
        transform_matrix_values : tuple of tuples of 16 floats
            transformation matrixes to be set as keyframes on the camera 'camera_name'
        """

        camera = bpy.data.objects[camera_name]
        for keyframe, matrix in zip(keyframes, transform_matrix_values):
            self.set_camera_transform(vcserver, camera_name, matrix)
            camera.keyframe_insert('location', frame=keyframe)
            camera.keyframe_insert('rotation_euler', frame=keyframe)
        bpy.ops.graph.virtucamera_euler_filter(object_name=camera_name)


    def remove_camera_keys(self, vcserver, camera_name):
        """ This method must remove all transform
        and focal length keyframes in the specified camera.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to remove the keyframes from.
        """

        camera = bpy.data.objects[camera_name]
        if camera.animation_data and camera.animation_data.action:
            for fcu in camera.animation_data.action.fcurves:
                if fcu.data_path in self.TRANSFORM_CHANNELS:
                    camera.animation_data.action.fcurves.remove(fcu)
        
        if camera.data.animation_data and camera.data.animation_data.action:
            for fcu in camera.data.animation_data.action.fcurves:
                if fcu.data_path == "lens":
                    camera.data.animation_data.action.fcurves.remove(fcu)
                    break


    def create_new_camera(self, vcserver):
        """ This method must create a new camera in the scene
        and return its name.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.

        Returns
        -------
        str
            Newly created camera name.
        """

        bpy.ops.object.camera_add(enter_editmode=False)
        return bpy.context.scene.objects[-1].name


    # VIEWPORT CAPTURE RELATED METHODS:
    # ---------------------------------

    def capture_will_start(self, vcserver):
        """ This method is called whenever a client app requests a video
        feed from the viewport. Usefull to init a pixel buffer
        or other objects you may need to capture the viewport

        IMPORTANT! Calling vcserver.set_capture_resolution() and
        vcserver.set_capture_mode() here is a must. Please check
        the documentation for those methods.

        You can also call vcserver.set_vertical_flip() here optionally,
        if you need to flip your pixel buffer. Disabled by default.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        """

        (x, y, width, height) = self.get_view_camera_rect()
        self.init_capture_buffer(width, height)
        vcserver.set_capture_resolution(width, height)
        vcserver.set_capture_mode(vcserver.CAPMODE_BUFFER_POINTER, vcserver.CAPFORMAT_UBYTE_BGRA)
        vcserver.set_vertical_flip(True)


    def capture_did_end(self, vcserver):
        """ Optional, this method is called whenever a client app
        stops the viewport video feed. Usefull to destroy a pixel buffer
        or other objects you may have created to capture the viewport.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        """

        del self.buffer


    def get_capture_pointer(self, vcserver, camera_name):
        """ If vcserver.capture_mode == vcserver.CAPMODE_BUFFER_POINTER,
        it must return an int representing a memory address to the first
        element of a contiguous buffer containing raw pixels of the 
        viewport image. The buffer must be kept allocated untill the next
        call to this function, is your responsability to do so.
        If you don't use CAPMODE_BUFFER_POINTER
        you don't need to overload this method.

        If the capture resolution has changed in size from the previous call to
        this method, vcserver.set_capture_resolution() must be called here
        before returning. You can use vcserver.capture_width and
        vcserver.capture_height to check the previous resolution.

        The name of the camera selected in the app is provided,
        as can be usefull to set-up the viewport render in some cases.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera that is currently selected in the App.

        Returns
        -------
        int
            value of the memory address to the first element of the buffer.
        """

        (x, y, width, height) = self.get_view_camera_rect()

        if width != vcserver.capture_width or height != vcserver.capture_height:
            vcserver.set_capture_resolution(width, height)
            self.init_capture_buffer(width, height)

        framebuffer = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glGetIntegerv(bgl.GL_DRAW_FRAMEBUFFER_BINDING, framebuffer)
        bgl.glBindFramebuffer(bgl.GL_FRAMEBUFFER, framebuffer[0])
        bgl.glReadPixels(x, y, width, height, bgl.GL_BGRA, bgl.GL_UNSIGNED_BYTE, self.buffer)
        return self.buffer_pointer


    def look_through_camera(self, vcserver, camera_name):
        """ This method must set the viewport to look through
        the specified camera.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        camera_name : str
            Name of the camera to look through
        """

        camera = bpy.data.objects[camera_name]
        context = bpy.context.scene.virtucamera.contexts['start']
        bpy.context.scene.camera = camera
        context['space_data'].region_3d.view_perspective = 'CAMERA'


    # APP/SERVER FEEDBACK METHODS:
    # ---------------------------

    def client_connected(self, vcserver, client_ip, client_port):
        """ Optional, this method is called whenever a client app
        connects to the server. Usefull to give the user
        feedback about a successfull connection.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        client_ip : str
            ip address of the remote client
        client_port : int
            port number of the remote client
        """

        bpy.ops.view3d.virtucamera_redraw()


    def client_disconnected(self, vcserver):
        """ Optional, this method is called whenever a client app
        disconnects from the server, even if it's disconnected by calling
        stop_serving() with the virtucamera.VCServer API. Usefull to give
        the user feedback about the disconnection.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        """

        bpy.ops.view3d.virtucamera_redraw()


    def current_camera_changed(self, vcserver, current_camera):
        """ Optional, this method is called when the user selects
        a different camera from the app. Usefull to give the user
        feedback about the currently selected camera.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        current_camera : str
            Name of the new selected camera
        """

        bpy.ops.view3d.virtucamera_redraw()


    def server_did_stop(self, vcserver):
        """ Optional, calling stop_serving() on virtucamera.VCServer
        doesn't instantly stop the server, it is done in the background
        due to the asyncronous nature of some of its processes.
        This method is called when all services have been completely
        stopped.

        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        """

        bpy.ops.view3d.virtucamera_redraw()


    # CUSTOM SCRIPT METHODS:
    # ----------------------

    def get_script_labels(self, vcserver):
        """ Optionally Return a list or tuple of str with the labels of
        custom scripts to be called from VirtuCamera App. Each label is
        a string that identifies the script that will be showed
        as a button in the App.
        The order of the labels is important. Later if the App asks
        to execute a script, an index based on this order will be provided
        to VCBase.execute_script(), so that method must also be implemented.
        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        Returns
        -------
        tuple or list of str
            custom script labels.
        """
        script_files = self.get_script_files()

        labels = []
        for filepath in script_files:
            filename = os.path.split(filepath)[1]
            tokens = filename.split("_")
            if len(tokens) > 1 and tokens[0].isdigit():
                prefix_len = len(tokens[0])
                label = filename[prefix_len+1:-3]
                labels.append(label)
            else:
                label = filename[:-3]
                labels.append(label)

        return labels
                

    def execute_script(self, vcserver, script_index, current_camera):
        """ Only required if VCBase.get_script_labels()
        has been implemented. This method is called whenever the user
        taps on a custom script button in the app.
        
        Each of the labels returned from VCBase.get_script_labels()
        identify a custom script that is showed as a button in the app.
        The order of the labels is important and 'script_index' is a 0-based
        index representing what script to execute from that list/tuple.
        This function must return True if the script executed correctly,
        False if there where errors. It's recommended to print any errors,
        so that the user has some feedback about what went wrong.
        You may want to provide a way for the user to refer to the currently
        selected camera in their scripts, so that they can act over it.
        'current_camera' is provided for this situation.
        Parameters
        ----------
        vcserver : virtucamera.VCServer object
            Instance of virtucamera.VCServer calling this method.
        script_index : int
            Script number to be executed.
        current_camera : str
            Name of the currently selected camera
        """
        script_files = self.get_script_files()

        if script_index >= len(script_files):
            print("Can't execute script "+str(script_index+1)+". Reason: Script doesn't exist")
            return False

        try:
            with open(script_files[script_index], "r") as script_file:
                script_code = script_file.read()
        except:
            traceback.print_exc()
            print("Can't execute script "+str(script_index+1)+". Reason: Unable to open file '"+script_files[script_index])+"'"
            return False

        if script_code == '':
            print("Can't execute script "+str(script_index+1)+". Reason: Empty script")
            return False

        selcam_var_def = 'vc_selcam = "'+current_camera+'"\n'
        script_code = selcam_var_def + script_code
        # use try to prevent any possible errors in the script from stopping plug-in execution
        try:
            exec(script_code)
            return True
        except:
            # Print traceback to inform the user
            traceback.print_exc()
            return False


# Blender will call this function regularly to keep VCServer working in the background
def timer_function():
    vcserver = bpy.context.scene.virtucamera.server
    vcserver.execute_pending_events()
    if vcserver.is_event_loop_running:
        return 0.0 # Call again as soon as possible
    else:
        return None # Stop calling this function

# Called whenever the scripts directory path changes
def update_script_labels(self, context):
    vcserver = bpy.context.scene.virtucamera.server
    vcserver.update_script_labels()

class VirtuCameraState(bpy.types.PropertyGroup):
    tcp_port: bpy.props.IntProperty(
        name = "Server TCP Port",
        description = "TCP port to listen for VirtuCamera App connections",
        default = 23354,
        min = 0,
        max = 65535
    )
    custom_scripts_dir: bpy.props.StringProperty(
        name = "Scripts",
        description = "Path to directory containing custom Python scripts to be shown as buttons in the app.\nIf you prefix file names with a number, it will be used to order the buttons\n(e.g.: 1_myscript.py)",
        default = "",
        subtype = "DIR_PATH",
        update = update_script_labels
    )
    server = VCServer(
        platform = "Blender",
        plugin_version = plugin_version,
        event_mode = VCServer.EVENTMODE_PULL,
        vcbase = VirtuCameraBlender()
    )
    custom_icons = bpy.utils.previews.new()
    contexts = dict()

class VIEW3D_OT_virtucamera_start(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_start"
    bl_label = "Start Serving"
    bl_description ="Start listening for incoming connections, then you can scan the QR Code from the App"

    @classmethod
    def poll(cls, context):
        server = context.scene.virtucamera.server
        return not server.is_serving

    def execute(self, context):
        state = context.scene.virtucamera
        server = state.server
        server.start_serving(state.tcp_port)
        if not server.is_serving:
            return {'FINISHED'}
        bpy.app.timers.register(timer_function)
        file_path = os.path.join(os.path.dirname(__file__), 'virtucamera_qr_img.png')
        server.write_qr_image_png(file_path, 3)
        state.custom_icons.clear()
        state.custom_icons.load('qr_image', file_path, 'IMAGE')
        state.contexts['start'] = context.copy()
        return {'FINISHED'}

class VIEW3D_OT_virtucamera_stop(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_stop"
    bl_label = "Stop Serving"
    bl_description ="Stop listening for incoming connections from VirtuCamera App"

    @classmethod
    def poll(cls, context):
        server = context.scene.virtucamera.server
        return server.is_serving

    def execute(self, context):
        server = context.scene.virtucamera.server
        server.stop_serving()
        return {'FINISHED'}

class VIEW3D_OT_virtucamera_redraw(bpy.types.Operator):
    bl_idname = "view3d.virtucamera_redraw"
    bl_label = "Redraw UI"

    def execute(self, context):
        for window in context.window_manager.windows:
            screen = window.screen
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}

# Wrapper to apply Blender's Euler Filter on specified camera object from any context
class GRAPH_OT_virtucamera_euler_filter(bpy.types.Operator):
    bl_idname = "graph.virtucamera_euler_filter"
    bl_label = "Euler Filter"

    object_name: bpy.props.StringProperty(name="Object Name")

    def execute(self, context):
        camera = bpy.data.objects[self.object_name]
        area = context.window_manager.windows[0].screen.areas[0]
        prev_cam_select = camera.select_get()
        prev_area_type = area.type
        try:
            camera.select_set(True)
            area.type = 'GRAPH_EDITOR'
            override = context.copy()
            override['area'] = area
            fcurves = [fcu for fcu in camera.animation_data.action.fcurves if fcu.data_path == 'rotation_euler']
            override['selected_visible_fcurves'] = fcurves
            bpy.ops.graph.euler_filter(override)
        except:
            area.type = prev_area_type
            camera.select_set(prev_cam_select)
            raise
        area.type = prev_area_type
        camera.select_set(prev_cam_select)
        return {'FINISHED'}

class VIEW3D_PT_virtucamera_main(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_virtucamera_main"
    bl_label = 'VirtuCamera'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VirtuCamera'

    def draw(self, context):
        state = context.scene.virtucamera
        server = state.server
        layout = self.layout
        column=layout.column()
        column.label(text='v%d.%d.%d (server v%d.%d.%d)' % (plugin_version + server.SERVER_VERSION))
        row = layout.row()
        if server.is_serving:
            row.enabled = False
        row.prop(state, "tcp_port")
        layout.operator('view3d.virtucamera_start')
        layout.operator('view3d.virtucamera_stop')
        if server.is_serving and not server.is_connected and 'qr_image' in state.custom_icons:
            column=layout.column()
            column.label(text='Server Ready')
            column.label(text='Connect through the App')
            layout.template_icon(icon_value=state.custom_icons['qr_image'].icon_id, scale=6)
        elif server.is_connected:
            column=layout.column()
            column.label(text='Connected: '+server.client_ip, icon='CHECKMARK')
            if server.current_camera:
                column.label(text=server.current_camera, icon='VIEW_CAMERA')
        column = layout.column()
        column.separator()
        column.prop(state, "custom_scripts_dir")
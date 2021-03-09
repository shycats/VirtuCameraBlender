# VirtuCameraBlender
# Copyright (c) 2021 Pablo Javier Garcia Gonzalez.
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

# Blender modules
import bpy
import bpy.utils.previews
import bgl
import mathutils

# VirtuCamera core lib
from . import virtucamera

plugin_version = (1, 0, 0)

class VirtuCameraBlender(virtucamera.Server):
    _TRANSFORM_CHANNELS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle")
    _B_TO_V_ROTATION_MAT = mathutils.Matrix((
        (1, 0, 0, 0),
        (0, 0,-1, 0),
        (0, 1, 0, 0),
        (0, 0, 0, 1)
    ))
    _V_TO_B_ROTATION_MAT = mathutils.Matrix((
        (1, 0, 0, 0),
        (0, 0, 1, 0),
        (0,-1, 0, 0),
        (0, 0, 0, 1)
    ))

    def view_zoom_factor(self, zoom_value):
        return ((zoom_value / 50 + math.sqrt(2)) / 2) ** 2

    def view_region_width_zoom_factor(self, zoom_factor, region_aspect_ratio, camera_aspect_ratio):
        return zoom_factor * min(camera_aspect_ratio, 1) / min(region_aspect_ratio, 1)

    def view_offset_factor(self, offset_value, zoom_factor):
        return offset_value * zoom_factor * -2

    # FIXME!!: Check if zoom, offset, resolution, aspect or region size/pos changes and return cached rect otherwise
    def get_view_camera_rect(self):
        scene = bpy.context.scene
        render = scene.render
        context = scene.virtucamera.contexts['start']
        region = context['region']
        r3d = context['space_data'].region_3d

        offset_value_x = r3d.view_camera_offset[0]
        offset_value_y = r3d.view_camera_offset[1]
        zoom_value = r3d.view_camera_zoom

        camera_aspect_ratio = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
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

        return (x, y, width, height)

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

    # Override this function to make it return what Blender Timer expects
    def execute_pending_callbacks(self):
        super().execute_pending_callbacks()
        if self.is_event_loop_running:
            return 0.0 # Call again as soon as possible
        else:
            return None # Stop calling this function

    # ------------------------------------------------------------------------
    #    GETTER CALLBACKS
    # ------------------------------------------------------------------------

    # Must return a tuple with (current_frame, range_start, range_end)
    def get_playback_state(self):
        current_frame = bpy.context.scene.frame_current
        range_start = bpy.context.scene.frame_start
        range_end = bpy.context.scene.frame_end
        return (current_frame, range_start, range_end)

    # Must return a float with the focal length value for the specified camera
    def get_camera_focal_length(self, camera_name):
        camera_data = bpy.data.objects[camera_name].data
        return camera_data.lens

    # Must return a list of camera names in the scene
    def get_scene_cameras(self):
        scene_cameras = []
        for obj in bpy.data.objects:
            if obj.type == "CAMERA":
                scene_cameras.append(obj)
        camera_names = [camera.name for camera in scene_cameras]
        return camera_names

    # Must return a tuple or list with the 4x4 transform matrix of the specified camera
    def get_camera_matrix(self, camera_name):
        camera_matrix = bpy.data.objects[camera_name].matrix_local.transposed()
        # VirtuCamera is Y+ up axis while Blender is Z+ up, so we rotate the transform matrix
        camera_matrix @= self._B_TO_V_ROTATION_MAT
        camera_matrix_tuple = (
            *camera_matrix[0],
            *camera_matrix[1],
            *camera_matrix[2],
            *camera_matrix[3]
        )
        return camera_matrix_tuple

    # If capture_mode == CAPMODE_IMAGE_BGRA_CPTR,
    # it must return an integer value with the memory address to the BGRA buffer
    def get_capture_pointer(self, camera_name):
        (x, y, width, height) = self.get_view_camera_rect()

        # If resolution has changed, we need to notify Virtucamera.Server the new resolution
        # with set_capture_resolution() and create a new Blender capture buffer
        if width != self.capture_width or height != self.capture_height:
            self.set_capture_resolution(width, height)
            self.init_capture_buffer(width, height)

        framebuffer = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glGetIntegerv(bgl.GL_DRAW_FRAMEBUFFER_BINDING, framebuffer)
        bgl.glBindFramebuffer(bgl.GL_FRAMEBUFFER, framebuffer[0])
        bgl.glReadPixels(x, y, width, height, bgl.GL_BGRA, bgl.GL_UNSIGNED_BYTE, self.buffer)
        return self.buffer_pointer

    # Must return a float value with scene playback FPS
    def get_play_fps(self):
        return bpy.context.scene.render.fps

    # Must return a tuple with (transform_has_keys, focal_length_has_keys)
    def get_camera_has_keys(self, camera_name):
        camera = bpy.data.objects[camera_name]
        
        transform_has_keys = False
        if camera.animation_data and camera.animation_data.action:
            for fcu in camera.animation_data.action.fcurves:
                if fcu.data_path in self._TRANSFORM_CHANNELS:
                    transform_has_keys = True
                    break
        
        focal_length_has_keys = False
        if camera.data.animation_data and camera.data.animation_data.action:
            for fcu in camera.data.animation_data.action.fcurves:
                if fcu.data_path == "lens":
                    focal_length_has_keys = True
                    break

        return (transform_has_keys, focal_length_has_keys)

    # Return True if the specified camera_name exists in the scene
    def get_camera_exists(self, camera_name):
        return camera_name in bpy.data.objects

    # ------------------------------------------------------------------------
    #    SETTER CALLBACKS
    # ------------------------------------------------------------------------

    def set_playback_range(self, start, end):
        bpy.context.scene.frame_start = start
        bpy.context.scene.frame_end = end

    def set_frame(self, frame):
        bpy.context.scene.frame_current = frame

    def set_camera_matrix(self, camera_name, transform_matrix):
        camera = bpy.data.objects[camera_name]
        matrix = mathutils.Matrix((
            transform_matrix[0:4],
            transform_matrix[4:8],
            transform_matrix[8:12],
            transform_matrix[12:16]
        ))
        matrix @= self._V_TO_B_ROTATION_MAT
        matrix.transpose()
        camera.matrix_local = matrix

    def set_camera_flen(self, camera_name, focal_length):
        camera_data = bpy.data.objects[camera_name].data
        camera_data.lens = focal_length

    def set_camera_matrix_keys(self, camera_name, keyframes, transform_matrix_values):
        camera = bpy.data.objects[camera_name]
        for keyframe, matrix in zip(keyframes, transform_matrix_values):
            self.set_camera_matrix(camera_name, matrix)
            camera.keyframe_insert('location', frame=keyframe)
            camera.keyframe_insert('rotation_euler', frame=keyframe)
        bpy.ops.graph.virtucamera_euler_filter(object_name=camera_name)


    def set_camera_flen_keys(self, camera_name, keyframes, focal_length_values):
        camera_data = bpy.data.objects[camera_name].data
        for keyframe, focal_length in zip(keyframes, focal_length_values):
            camera_data.lens = focal_length
            camera_data.keyframe_insert('lens', frame=keyframe)

    # ------------------------------------------------------------------------
    #    ACTION CALLBACKS
    # ------------------------------------------------------------------------

    def client_connected(self, client_ip, client_port):
        bpy.ops.view3d.virtucamera_redraw()

    def client_disconnected(self):
        bpy.ops.view3d.virtucamera_redraw()

    def current_camera_changed(self, current_camera):
        bpy.ops.view3d.virtucamera_redraw()

    # Calling set_capture_resolution() and set_capture_mode() here is mandatory.
    # You can call set_vertical_flip() here optionally, by default is False.
    def streaming_will_start(self):
        # Workaround to support multiprocessing in Blender 2.91+
        # as it's broken. sys.executable points to the
        # Python executable instead of the Blender executable, so
        # we revert it back.
        sys.executable = bpy.app.binary_path

        (x, y, width, height) = self.get_view_camera_rect()
        self.set_capture_resolution(width, height)
        self.set_capture_mode(self.CAPMODE_IMAGE_BGRA_CPTR)
        self.set_vertical_flip(True)
        self.init_capture_buffer(width, height)

    def streaming_did_end(self):
        del self.buffer

    def start_playback(self, forward):
        if not bpy.context.screen.is_animation_playing:
            # This operator acts like a toggle, so we need to first check if it's playing
            bpy.ops.screen.animation_play(reverse=(not forward), sync=True)

    def stop_playback(self):
        bpy.ops.screen.animation_cancel(restore_frame=False)

    def switch_playback(self, forward):
        # This operator acts like a toggle, so it actually switches playback state.
        bpy.ops.screen.animation_play(reverse=(not forward), sync=True)

    def remove_camera_keys(self, camera_name):
        camera = bpy.data.objects[camera_name]
        if camera.animation_data and camera.animation_data.action:
            for fcu in camera.animation_data.action.fcurves:
                if fcu.data_path in self._TRANSFORM_CHANNELS:
                    camera.animation_data.action.fcurves.remove(fcu)
        
        if camera.data.animation_data and camera.data.animation_data.action:
            for fcu in camera.data.animation_data.action.fcurves:
                if fcu.data_path == "lens":
                    camera.data.animation_data.action.fcurves.remove(fcu)
                    break

    # Must return the new camera_name
    def create_new_camera(self):
        bpy.ops.object.camera_add(enter_editmode=False)
        return bpy.context.scene.objects[-1].name

    def look_through_camera(self, camera_name):
        camera = bpy.data.objects[camera_name]
        context = bpy.context.scene.virtucamera.contexts['start']
        context['scene'].camera = camera
        context['space_data'].region_3d.view_perspective = 'CAMERA'


class VirtuCameraState(bpy.types.PropertyGroup):
    tcp_port: bpy.props.IntProperty(
        name = "Server TCP Port",
        description = "TCP port to listen for VirtuCamera App connections",
        default = 23354,
        min = 0,
        max = 65535
    )
    server = VirtuCameraBlender(
        platform = "Blender",
        plugin_version = plugin_version,
        event_mode = VirtuCameraBlender.EVENTMODE_PULL
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
        bpy.app.timers.register(server.execute_pending_callbacks)
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

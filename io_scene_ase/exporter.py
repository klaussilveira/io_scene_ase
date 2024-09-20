import os.path
from typing import Iterable, List, Set, Union, cast, Optional

import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, CollectionProperty, PointerProperty, IntProperty, EnumProperty, BoolProperty
from bpy.types import Operator, Material, PropertyGroup, UIList, Object, FileHandler, Event, Context, SpaceProperties, \
    Collection
from mathutils import Matrix, Vector

from .builder import ASEBuildOptions, ASEBuildError, get_mesh_objects, build_ase
from .writer import ASEWriter


class ASE_PG_material(PropertyGroup):
    material: PointerProperty(type=Material)


class ASE_PG_string(PropertyGroup):
    string: StringProperty()


def get_vertex_color_attributes_from_objects(objects: Iterable[Object]) -> Set[str]:
    '''
    Get the unique vertex color attributes from all the selected objects.
    :param objects: The objects to search for vertex color attributes.
    :return: A set of unique vertex color attributes.
    '''
    items = set()
    for obj in filter(lambda x: x.type == 'MESH', objects):
        for layer in filter(lambda x: x.domain == 'CORNER', obj.data.color_attributes):
            items.add(layer.name)
    return items


def vertex_color_attribute_items(self, context):
    # Get the unique color attributes from all the selected objects.
    return [(x, x, '') for x in sorted(get_vertex_color_attributes_from_objects(context.selected_objects))]


class ASE_PG_export(PropertyGroup):
    material_list: CollectionProperty(name='Materials', type=ASE_PG_material)
    material_list_index: IntProperty(name='Index', default=0)
    should_export_vertex_colors: BoolProperty(name='Export Vertex Colors', default=True)
    vertex_color_mode: EnumProperty(name='Vertex Color Mode', items=(
        ('ACTIVE', 'Active', 'Use the active vertex color attribute'),
        ('EXPLICIT', 'Explicit', 'Use the vertex color attribute specified below'),
    ))
    has_vertex_colors: BoolProperty(name='Has Vertex Colors', default=False, options={'HIDDEN'})
    vertex_color_attribute: EnumProperty(name='Attribute', items=vertex_color_attribute_items)
    should_invert_normals: BoolProperty(name='Invert Normals', default=False, description='Invert the normals of the exported geometry. This should be used if the software you are exporting to uses a different winding order than Blender')


def get_unique_materials(mesh_objects: Iterable[Object]) -> List[Material]:
    materials = set()
    for mesh_object in mesh_objects:
        for i, material_slot in enumerate(mesh_object.material_slots):
            material = material_slot.material
            if material is None:
                raise RuntimeError(f'Material slots cannot be empty ({mesh_object.name}, material slot index {i})')
            materials.add(material)
    return list(materials)


def populate_material_list(mesh_objects: Iterable[Object], material_list):
    materials = get_unique_materials(mesh_objects)
    material_list.clear()
    for index, material in enumerate(materials):
        m = material_list.add()
        m.material = material
        m.index = index


def get_collection_from_context(context: Context) -> Optional[Collection]:
    if context.space_data.type != 'PROPERTIES':
        return None

    space_data = cast(SpaceProperties, context.space_data)

    if space_data.use_pin_id:
        return cast(Collection, space_data.pin_id)
    else:
        return context.collection


def get_collection_export_operator_from_context(context: Context) -> Optional['ASE_OT_export_collection']:
    collection = get_collection_from_context(context)
    if collection is None:
        return None
    if 0 > collection.active_exporter_index >= len(collection.exporters):
        return None
    exporter = collection.exporters[collection.active_exporter_index]
    # TODO: make sure this is actually an ASE exporter.
    return exporter.export_properties


class ASE_OT_material_order_add(Operator):
    bl_idname = 'ase_export.material_order_add'
    bl_label = 'Add'
    bl_description = 'Add a material to the list'

    def invoke(self, context: Context, event: Event) -> Union[Set[str], Set[int]]:
        # TODO: get the region that this was invoked from and set the collection to the collection of the region.
        print(event)
        return self.execute(context)

    def execute(self, context: 'Context') -> Union[Set[str], Set[int]]:
        # Make sure this is being invoked from the properties region.
        operator = get_collection_export_operator_from_context(context)

        if operator is None:
            return {'INVALID_CONTEXT'}

        material_string = operator.material_order.add()
        material_string.string = 'Material'

        return {'FINISHED'}


class ASE_OT_material_order_remove(Operator):
    bl_idname = 'ase_export.material_order_remove'
    bl_label = 'Remove'
    bl_description = 'Remove the selected material from the list'

    @classmethod
    def poll(cls, context: Context):
        operator = get_collection_export_operator_from_context(context)
        if operator is None:
            return False
        return 0 <= operator.material_order_index < len(operator.material_order)

    def execute(self, context: 'Context') -> Union[Set[str], Set[int]]:
        operator = get_collection_export_operator_from_context(context)

        if operator is None:
            return {'INVALID_CONTEXT'}

        operator.material_order.remove(operator.material_order_index)

        return {'FINISHED'}


class ASE_OT_material_order_move_up(Operator):
    bl_idname = 'ase_export.material_order_move_up'
    bl_label = 'Move Up'
    bl_description = 'Move the selected material up one slot'

    @classmethod
    def poll(cls, context: Context):
        operator = get_collection_export_operator_from_context(context)
        if operator is None:
            return False
        return operator.material_order_index > 0

    def execute(self, context: 'Context') -> Union[Set[str], Set[int]]:
        operator = get_collection_export_operator_from_context(context)

        if operator is None:
            return {'INVALID_CONTEXT'}

        operator.material_order.move(operator.material_order_index, operator.material_order_index - 1)
        operator.material_order_index -= 1

        return {'FINISHED'}


class ASE_OT_material_order_move_down(Operator):
    bl_idname = 'ase_export.material_order_move_down'
    bl_label = 'Move Down'
    bl_description = 'Move the selected material down one slot'

    @classmethod
    def poll(cls, context: Context):
        operator = get_collection_export_operator_from_context(context)
        if operator is None:
            return False
        return operator.material_order_index < len(operator.material_order) - 1

    def execute(self, context: 'Context') -> Union[Set[str], Set[int]]:
        operator = get_collection_export_operator_from_context(context)

        if operator is None:
            return {'INVALID_CONTEXT'}

        operator.material_order.move(operator.material_order_index, operator.material_order_index + 1)
        operator.material_order_index += 1

        return {'FINISHED'}


class ASE_OT_material_list_move_up(Operator):
    bl_idname = 'ase_export.material_list_item_move_up'
    bl_label = 'Move Up'
    bl_options = {'INTERNAL'}
    bl_description = 'Move the selected material up one slot'

    @classmethod
    def poll(cls, context):
        pg = getattr(context.scene, 'ase_export')
        return pg.material_list_index > 0

    def execute(self, context):
        pg = getattr(context.scene, 'ase_export')
        pg.material_list.move(pg.material_list_index, pg.material_list_index - 1)
        pg.material_list_index -= 1
        return {'FINISHED'}


class ASE_OT_material_list_move_down(Operator):
    bl_idname = 'ase_export.material_list_item_move_down'
    bl_label = 'Move Down'
    bl_options = {'INTERNAL'}
    bl_description = 'Move the selected material down one slot'

    @classmethod
    def poll(cls, context):
        pg = getattr(context.scene, 'ase_export')
        return pg.material_list_index < len(pg.material_list) - 1

    def execute(self, context):
        pg = getattr(context.scene, 'ase_export')
        pg.material_list.move(pg.material_list_index, pg.material_list_index + 1)
        pg.material_list_index += 1
        return {'FINISHED'}


class ASE_UL_materials(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.prop(item.material, 'name', text='', emboss=False, icon_value=layout.icon(item.material))


class ASE_UL_strings(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.prop(item, 'string', text='', emboss=False)



object_eval_state_items = (
    ('EVALUATED', 'Evaluated', 'Use data from fully evaluated object'),
    ('ORIGINAL', 'Original', 'Use data from original object with no modifiers applied'),
)


class ASE_OT_export(Operator, ExportHelper):
    bl_idname = 'io_scene_ase.ase_export'
    bl_label = 'Export ASE'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_description = 'Export selected objects to ASE'
    filename_ext = '.ase'
    filter_glob: StringProperty(default="*.ase", options={'HIDDEN'}, maxlen=255)
    object_eval_state: EnumProperty(
        items=object_eval_state_items,
        name='Data',
        default='EVALUATED'
    )

    @classmethod
    def poll(cls, context):
        if not any(x.type == 'MESH' for x in context.selected_objects):
            cls.poll_message_set('At least one mesh must be selected')
            return False
        return True

    def draw(self, context):
        layout = self.layout
        pg = context.scene.ase_export

        materials_header, materials_panel = layout.panel('Materials', default_closed=False)
        materials_header.label(text='Materials')

        if materials_panel:
            row = materials_panel.row()
            row.template_list('ASE_UL_materials', '', pg, 'material_list', pg, 'material_list_index')
            col = row.column(align=True)
            col.operator(ASE_OT_material_list_move_up.bl_idname, icon='TRIA_UP', text='')
            col.operator(ASE_OT_material_list_move_down.bl_idname, icon='TRIA_DOWN', text='')


        has_vertex_colors = len(get_vertex_color_attributes_from_objects(context.selected_objects)) > 0
        vertex_colors_header, vertex_colors_panel = layout.panel_prop(pg, 'should_export_vertex_colors')
        row = vertex_colors_header.row()
        row.enabled = has_vertex_colors
        row.prop(pg, 'should_export_vertex_colors', text='Vertex Colors')

        if vertex_colors_panel:
            vertex_colors_panel.use_property_split = True
            vertex_colors_panel.use_property_decorate = False
            if has_vertex_colors:
                vertex_colors_panel.prop(pg, 'vertex_color_mode', text='Mode')
                if pg.vertex_color_mode == 'EXPLICIT':
                    vertex_colors_panel.prop(pg, 'vertex_color_attribute', icon='GROUP_VCOL')
            else:
                vertex_colors_panel.label(text='No vertex color attributes found')

        advanced_header, advanced_panel = layout.panel('Advanced', default_closed=True)
        advanced_header.label(text='Advanced')

        if advanced_panel:
            advanced_panel.use_property_split = True
            advanced_panel.use_property_decorate = False
            advanced_panel.prop(self, 'object_eval_state')
            advanced_panel.prop(pg, 'should_invert_normals')

    def invoke(self, context: 'Context', event: 'Event' ) -> Union[Set[str], Set[int]]:
        mesh_objects = [x[0] for x in get_mesh_objects(context.selected_objects)]

        pg = getattr(context.scene, 'ase_export')

        try:
            populate_material_list(mesh_objects, pg.material_list)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        self.filepath = f'{context.active_object.name}.ase'

        context.window_manager.fileselect_add(self)

        return {'RUNNING_MODAL'}

    def execute(self, context):
        pg = getattr(context.scene, 'ase_export')

        options = ASEBuildOptions()
        options.object_eval_state = self.object_eval_state
        options.should_export_vertex_colors = pg.should_export_vertex_colors
        options.vertex_color_mode = pg.vertex_color_mode
        options.has_vertex_colors = len(get_vertex_color_attributes_from_objects(context.selected_objects)) > 0
        options.vertex_color_attribute = pg.vertex_color_attribute
        options.materials = [x.material for x in pg.material_list]
        options.should_invert_normals = pg.should_invert_normals
        try:
            ase = build_ase(context, options, context.selected_objects)
            ASEWriter().write(self.filepath, ase)
            self.report({'INFO'}, 'ASE exported successfully')
            return {'FINISHED'}
        except ASEBuildError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}


class ASE_OT_export_collection(Operator, ExportHelper):
    bl_idname = 'io_scene_ase.ase_export_collection'
    bl_label = 'Export collection to ASE'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_description = 'Export collection to ASE'
    filename_ext = '.ase'
    filter_glob: StringProperty(
        default="*.ase",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be highlighted.
    )
    object_eval_state: EnumProperty(
        items=object_eval_state_items,
        name='Data',
        default='EVALUATED'
    )

    collection: StringProperty()
    material_order: CollectionProperty(name='Materials', type=ASE_PG_string)
    material_order_index: IntProperty(name='Index', default=0)


    def draw(self, context):
        layout = self.layout

        materials_header, materials_panel = layout.panel('Materials', default_closed=False)
        materials_header.label(text='Materials')

        if materials_panel:
            row = materials_panel.row()
            row.template_list('ASE_UL_strings', '', self, 'material_order', self, 'material_order_index')
            col = row.column(align=True)
            col.operator(ASE_OT_material_order_add.bl_idname, icon='ADD', text='')
            col.operator(ASE_OT_material_order_remove.bl_idname, icon='REMOVE', text='')
            col.separator()
            col.operator(ASE_OT_material_order_move_up.bl_idname, icon='TRIA_UP', text='')
            col.operator(ASE_OT_material_order_move_down.bl_idname, icon='TRIA_DOWN', text='')

        advanced_header, advanced_panel = layout.panel('Advanced', default_closed=True)
        advanced_header.label(text='Advanced')

        if advanced_panel:
            advanced_panel.use_property_split = True
            advanced_panel.use_property_decorate = False
            advanced_panel.prop(self, 'object_eval_state')

    def execute(self, context):
        collection = bpy.data.collections.get(self.collection)

        options = ASEBuildOptions()
        options.object_eval_state = self.object_eval_state
        options.transform = Matrix.Translation(-Vector(collection.instance_offset))

        # Iterate over all the objects in the collection.
        mesh_objects = get_mesh_objects(collection.all_objects)

        # Get all the materials used by the objects in the collection.
        options.materials = get_unique_materials([x[0] for x in mesh_objects])

        # Sort the materials based on the order in the material order list, keeping in mind that the material order list
        # may not contain all the materials used by the objects in the collection.
        material_order = [x.string for x in self.material_order]
        material_order_map = {x: i for i, x in enumerate(material_order)}
        options.materials.sort(key=lambda x: material_order_map.get(x.name, len(material_order)))

        try:
            ase = build_ase(context, options, collection.all_objects)
        except ASEBuildError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        try:
            ASEWriter().write(self.filepath, ase)
        except PermissionError as e:
            self.report({'ERROR'}, 'ASCII Scene Export: ' + str(e))
            return {'CANCELLED'}

        return {'FINISHED'}


class ASE_FH_export(FileHandler):
    bl_idname = 'ASE_FH_export'
    bl_label = 'ASCII Scene Export'
    bl_export_operator = ASE_OT_export_collection.bl_idname
    bl_file_extensions = '.ase'



classes = (
    ASE_PG_material,
    ASE_PG_string,
    ASE_UL_materials,
    ASE_UL_strings,
    ASE_PG_export,
    ASE_OT_export,
    ASE_OT_export_collection,
    ASE_OT_material_list_move_down,
    ASE_OT_material_list_move_up,
    ASE_OT_material_order_add,
    ASE_OT_material_order_remove,
    ASE_OT_material_order_move_down,
    ASE_OT_material_order_move_up,
    ASE_FH_export,
)

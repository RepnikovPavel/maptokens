export const LAYER_CATEGORIES=[
 ['hd','HD lines'],['paint','Paintings'],['sign','Signs'],['light','Lights'],['pole','Poles'],['other','Other']
];
export function layerCategory(kind){
 const type=kind.split(':')[0];
 if(type.startsWith('traffic_sign'))return 'sign';
 if(type.includes('traffic_light'))return 'light';
 if(type==='pole')return 'pole';
 if(/arrow|symbol|painting|marking|crosswalk|zebra/.test(type))return 'paint';
 if(/^(line_thin|line_thick|stop_line|curbstone|road_border|guard_rail|virtual|lane|lanelet|centerline|drivable_space_border)$/.test(type))return 'hd';
 return 'other';
}
export class LayerVisibility {
 constructor(){this.defaults={camera:{hd:true,paint:true},map:{hd:true,paint:true}};this.overrides={camera:new Map(),map:new Map()};}
 enabled(kind,view){return this.overrides[view].get(kind)??!!this.defaults[view][layerCategory(kind)];}
 setType(kind,view,value){this.overrides[view].set(kind,value);}
 setCategory(category,view,value){this.defaults[view][category]=value;for(const kind of this.overrides[view].keys())if(layerCategory(kind)===category)this.overrides[view].delete(kind);}
}

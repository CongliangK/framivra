/** R06 source contract. SVG tree is the SVG-fragment equivalent; not a renderer API. */
export type Point = readonly [number, number];
export type Matrix = readonly [number, number, number, number, number, number];
export type SceneId = 'S1'|'S2'|'S3'|'S4'|'S5'|'S6'|'S7'|'S8';
export type LayerId = 'T02'|'T03'|'T04'|'T05'|'T06'|'T07'|'T08'|'T09'|'T10'|'T11'|'T12'|'T13'|'T14'|'T15';
export type EffectId = 'path_deformation'|'material_masks'|'text_reveal'|'translucent_trails'|'gradient_alpha_reveal'|'local_blur';
export interface SvgNode { readonly tag: string; readonly attrs: Readonly<Record<string,string>>; readonly children: readonly (SvgNode|string)[]; }
export interface SvgFragment { readonly defs: readonly SvgNode[]; readonly world: readonly SvgNode[]; readonly screen: readonly SvgNode[]; }
export interface Pose { readonly x: number; readonly y: number; readonly scale: number; readonly angle: number; }
export interface BirdState {
  readonly pose: Pose;
  readonly space: 'world'|'screen';
  readonly stage: 0|1|2|3|4;
  readonly wing: number;
  readonly feet: boolean;
  /** Plain resolved silhouettes in SAME world/screen coordinate space, no camera applied. */
  readonly occluders: readonly SvgNode[];
  readonly maskBounds?: readonly [number,number,number,number];
}
export interface SharedState {
  readonly cameraMatrix: Matrix;
  readonly bird: BirdState|null;
  /** Scene-local solved hinge/anchor/carrier graph, computed once from frame alone. */
  readonly mechanics?: unknown;
  readonly [key:string]: unknown;
}
export interface FrameArgs {
  readonly globalFrame: number;
  readonly localSceneFrame: number;
  readonly layerId: LayerId;
}
export interface Entry {
  readonly entryId: string; readonly layerId: LayerId; readonly role: string;
  readonly src: string; readonly windowStart: number; readonly duration: number; readonly paintIndex: number;
}
/** Helper surfaces are CommonJS modules with JSDoc and executable Node tests. */
export interface BaseContext {
  readonly assets: Readonly<Record<string,unknown>>;
  readonly math: typeof import('./runtime/math.js');
  readonly svg: typeof import('./runtime/svg.js');
  readonly geometry: typeof import('./runtime/geometry.js');
  readonly effectHelpers: typeof import('./runtime/effects.js');
  readonly opening: typeof import('./runtime/opening-state.js');
  readonly effects: Readonly<Record<EffectId,boolean>>;
}
export interface RenderContext extends BaseContext {
  readonly entry: Entry; readonly localFrame: number; readonly globalFrame: number;
  readonly localSceneFrame: number; readonly state: SharedState;
  /** f2940 throughout [2940,3090), actual global frame otherwise. */
  readonly stateFrame: number;
  readonly id: (name:string)=>string;
  readonly sharedAt: (globalFrame:number)=>SharedState;
  /** Exact emitted candidate timeline; this identity is not approval or readiness. */
  readonly sourceBindingStatus: 'ACTUAL_FINAL_TIMELINE';
  readonly sourceFields: readonly unknown[];
}
export interface SceneModule {
  readonly sceneId: SceneId;
  readonly implemented: boolean;
  /** No layer, entry, URL, DOM or prior state: identical across every owner page. */
  resolveSceneState(ctx:BaseContext,args:Omit<FrameArgs,'layerId'>):SharedState;
  /** T07/T08 are emitted by the mandatory shared bird path, not this author callback. */
  renderScene(ctx:RenderContext,args:FrameArgs):SvgFragment;
}
export function renderScene(ctx:RenderContext,args:FrameArgs):SvgFragment;

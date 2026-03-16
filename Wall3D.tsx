// src/components/scene/Wall3D.tsx
import React, { useMemo, useRef, useEffect } from "react";
import * as THREE from "three";
import { CM_TO_M, MODE_3D } from "../../../../utils/Constants";
import Hole3D from "./Hole3D";
import { HoleFiller } from "./HoleFiller";
import { Hole, Line, Vertex, Area, Structure } from "../../../../types/editorModels";
import {
  WallTextureData,
} from "../../../../utils/assetManager";
import { useFrame, useThree } from "@react-three/fiber";
import {
  convertHolesToShaderFormat,
} from '../../../../utils/wallHoleShader';
import { createWallMaterials } from "../../../../utils/wallMaterialUtils";
import {
  createMiteredWallGeometry3DFromCorners,
} from "../../../../utils/wallCornerUtils";
import { computeWallPolygon2D, MiterConnectionInfo } from "../../../../utils/wall2DMiterUtils";
import { setHighlight } from "../../../../utils/highlightUtils";

// --- TYPE DEFINITIONS ---
type Wall3DProps = {
  line: Line;
  vertices: Vertex[];
  holes: Hole[];
  structures?: Structure[];
  models: Map<string, THREE.Group>;
  wallTextures: WallTextureData | null;
  // ✅ MITER: Replaced prevVertex/nextVertex with proper miter info from 2D system
  startMiterInfo?: MiterConnectionInfo | null;
  endMiterInfo?: MiterConnectionInfo | null;
  wallIndex: number;
  isSelected?: boolean;
  selectedAreas?: string[];
  selectedHoles?: string[];
  hoveredHole?: string | null;
  hoveredWall?: string | null;
  areas?: Area[];
  onWallClick?: (
    wallId: string,
    clickPosition?: { x: number; y: number }
  ) => void;
  onHoleClick?: (
    holeId: string,
    clickPosition?: { x: number; y: number }
  ) => void;
  onHoleHover?: (holeId: string | null) => void;
  onHoleUpdate?: (holeId: string, updates: any) => void;
  onLineHover?: (LineId: string | null) => void;
  walkModeActive?: boolean;
  plannerMode?: string;
  wallTransparenceMode?: boolean;
  selectedLayerId?: string;
  showAllFloors?: boolean;
  currentFloorOpacity?: number;
  otherFloorsOpacity?: number;
};

// --- HELPER FUNCTIONS ---
/**
 * Calculates the angle of a line segment defined by two vertices.
 */
function getWallAngle(v1: Vertex, v2: Vertex): number {
  return Math.atan2(v2.y - v1.y, v2.x - v1.x);
}

// ... existing helpers removed for brevity ...

function getWallFacingDirection(
  line: Line,
  verticesMap: Map<string, Vertex>,
  areas: Area[]
): "inner-left" | "inner-right" | null {
  // ... existing implementation ...
  const [vA, vB] = line.vertices.map((id) => verticesMap.get(id));
  if (!vA || !vB) return null;

  // Find area that shares both vertices with this wall
  const area = areas.find((a) =>
    line.vertices.every((vId) => a.vertices.includes(vId))
  );
  if (!area) return null;

  const polygon = area.vertices
    .map((vId) => verticesMap.get(vId))
    .filter((v): v is Vertex => !!v)
    .map((v) => new THREE.Vector2(v.x, v.y));

  if (polygon.length < 3) return null;

  // Wall direction vector
  const dir = new THREE.Vector2(vB.x - vA.x, vB.y - vA.y).normalize();

  // Perpendicular vectors (left and right normals)
  const leftNormal = new THREE.Vector2(-dir.y, dir.x);
  const rightNormal = new THREE.Vector2(dir.y, -dir.x);

  // Wall midpoint
  const mid = new THREE.Vector2((vA.x + vB.x) / 2, (vA.y + vB.y) / 2);

  // Test a small offset (5 cm) on both sides
  const leftSample = mid.clone().addScaledVector(leftNormal, 5);
  const rightSample = mid.clone().addScaledVector(rightNormal, 5);

  const leftInside = isPointInPolygon(leftSample, polygon);
  const rightInside = isPointInPolygon(rightSample, polygon);

  // Decide based purely on actual geometry (no winding assumption)
  if (leftInside && !rightInside) return "inner-left";
  if (rightInside && !leftInside) return "inner-right";

  // Fallback (rarely happens if wall lies exactly on polygon border)
  return "inner-left";
}

// Basic point-in-polygon test
function isPointInPolygon(
  point: THREE.Vector2,
  polygon: THREE.Vector2[]
): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i].x,
      yi = polygon[i].y;
    const xj = polygon[j].x,
      yj = polygon[j].y;
    const intersect =
      yi > point.y !== yj > point.y &&
      point.x < ((xj - xi) * (point.y - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

/**
 * Creates a triangular prism geometry to fill the junction fan gap at 3+ wall junctions.
 * Matches the 2D polygon's center vertex fan: startLeft → centerVertex → startRight.
 * Uses per-face vertex duplication so computeVertexNormals gives flat normals.
 */
function createJunctionFanGeometry(
  frontX: number,
  backX: number,
  centerX: number,
  halfThickness: number,
  halfHeight: number
): THREE.BufferGeometry | null {
  const frontDelta = Math.abs(frontX - centerX);
  const backDelta = Math.abs(backX - centerX);
  if (frontDelta < 0.001 && backDelta < 0.001) return null;

  // Corner positions: A=front, B=center, C=back
  const A = { bx: frontX, tz: halfThickness };
  const B = { bx: centerX, tz: 0 };
  const C = { bx: backX, tz: -halfThickness };
  const hH = halfHeight;

  // Per-face vertices (no sharing between faces → flat normals from computeVertexNormals)
  const verts: number[] = [];
  const idxs: number[] = [];
  let vi = 0;

  const push = (x: number, y: number, z: number) => { verts.push(x, y, z); };

  // -- TOP face (3 verts) --
  push(A.bx, hH, A.tz);  // 0
  push(B.bx, hH, B.tz);  // 1
  push(C.bx, hH, C.tz);  // 2
  idxs.push(vi, vi + 1, vi + 2);
  vi += 3;

  // -- BOTTOM face (3 verts, reversed winding) --
  push(A.bx, -hH, A.tz);  // 3
  push(C.bx, -hH, C.tz);  // 4
  push(B.bx, -hH, B.tz);  // 5
  idxs.push(vi, vi + 1, vi + 2);
  vi += 3;

  // -- SIDE AB: front corner to center (4 verts, 2 tris) --
  push(A.bx, -hH, A.tz);  // 6
  push(B.bx, -hH, B.tz);  // 7
  push(B.bx, hH, B.tz);  // 8
  push(A.bx, hH, A.tz);  // 9
  idxs.push(vi, vi + 1, vi + 2, vi, vi + 2, vi + 3);
  vi += 4;

  // -- SIDE BC: center to back corner (4 verts, 2 tris) --
  push(B.bx, -hH, B.tz);  // 10
  push(C.bx, -hH, C.tz);  // 11
  push(C.bx, hH, C.tz);  // 12
  push(B.bx, hH, B.tz);  // 13
  idxs.push(vi, vi + 1, vi + 2, vi, vi + 2, vi + 3);

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  geometry.setIndex(idxs);
  geometry.computeVertexNormals();

  return geometry;
}

// --- MAIN COMPONENT ---
// ⚡ PERFORMANCE: Component is memoized at the end of file with custom comparator
const Wall3D: React.FC<Wall3DProps> = ({
  line,
  vertices = [],
  holes,
  structures = [],
  models,
  wallTextures,
  startMiterInfo = null,
  endMiterInfo = null,
  wallIndex,
  isSelected = false,
  selectedAreas = [],
  selectedHoles = [],
  hoveredHole = null,
  // hoveredWall = null,
  areas = [],
  onWallClick,
  onHoleClick,
  onHoleHover,
  onHoleUpdate,
  onLineHover,
  walkModeActive = false,
  plannerMode = '',
  wallTransparenceMode = false,
  selectedLayerId,
  showAllFloors = false,
  // currentFloorOpacity = 1,
  // otherFloorsOpacity = 0.65,
}) => {
  const is3DEditMode = false;
  const isEditable = (plannerMode === MODE_3D)
    ? (!showAllFloors || (line as any).layerId === selectedLayerId)
    : true;
  const { camera } = useThree();

  const wallGroupRef = useRef<THREE.Group>(null);
  const outerMaterialRef = useRef<THREE.Material | null>(null);
  const topMaterialRef = useRef<THREE.MeshStandardMaterial | null>(null);
  const innerMaterialRef = useRef<THREE.Material | null>(null);
  const wallFacingState = useRef<"inner-left" | "inner-right" | null>(null);
  const worldPos = useMemo(() => new THREE.Vector3(), []);
  const worldQuat = useMemo(() => new THREE.Quaternion(), []);
  const worldNormal = useMemo(() => new THREE.Vector3(), []);
  const cameraVec = useMemo(() => new THREE.Vector3(), []);
  // const [isWallFaded, setWallFaded] = useState(false);
  const wallFadedRef = useRef(false);
  // ✅ REMOVED: getOverlappingWallHoles() function
  // This function created duplicate holes on overlapping walls, causing confusion.
  // Now each wall only renders its own holes for cleaner, more predictable behavior.

  // ✅ NICHE MAPPING: Convert intersecting Niche structures to holes for shader


  const is3DMode = useMemo(() => plannerMode === MODE_3D, [plannerMode]);
  const syntheticHoles = useMemo(() => {
    if (!structures || structures.length === 0) return [];

    // Find vertices
    const vA = vertices.find(v => v.id === line.vertices[0]);
    const vB = vertices.find(v => v.id === line.vertices[1]);
    if (!vA || !vB) return [];

    const wallVec = new THREE.Vector2(vB.x - vA.x, vB.y - vA.y);
    const wallLen = wallVec.length();
    if (wallLen < 0.001) return []; // zero length wall

    const wallDir = wallVec.clone().normalize();

    return structures
      .filter(s => s.structure_type === "niche")
      .map(s => {
        // Project s position onto wall line
        // Niche structure (s.x, s.y) is global center
        const structVec = new THREE.Vector2(s.x - vA.x, s.y - vA.y);
        const projection = structVec.dot(wallDir); // distance along wall from A
        const perpDist = Math.abs(structVec.cross(wallDir)); // distance from wall line

        // Threshold: assume Niche is meant for this wall if close enough
        // Using wall thickness as rough bound or fixed 20cm
        const thickness = line.properties?.thickness?.length ?? 20;

        if (projection >= 0 && projection <= wallLen && perpDist < (thickness / 2 + 25)) {
          // It's on/in the wall
          // Create synthetic hole
          return {
            id: `niche-fake-hole-${s.id}`,
            name: "Niche Hole",
            line: line.id,
            type: "niche",
            offset: projection,
            properties: {
              width: { length: s.properties.width.length },
              height: { length: s.properties.height.length },
              altitude: { length: s.properties.altitude.length },
              thickness: { length: 0 }, // irrelevant for 2D hole logic, but pass 0
            },
            // Niche is not a "real" hole in store, so no extras properties
          } as unknown as Hole; // Cast to Hole to satisfy type checker
        }
        return null;
      })
      .filter((h): h is Hole => h !== null);
  }, [structures, line, vertices]);

  // Merge real and synthetic holes
  const allRelatedHoles = useMemo(() => [...holes, ...syntheticHoles], [holes, syntheticHoles]);

  const wallData = useMemo(() => {
    const verticesMap = new Map(vertices.map((v) => [v.id, v]));

    const wallFacing = getWallFacingDirection(line, verticesMap, areas);
    wallFacingState.current = wallFacing;
    const vA = verticesMap.get(line.vertices[0]);
    const vB = verticesMap.get(line.vertices[1]);

    if (!vA || !vB) return null;

    // Check if this wall belongs to any selected area - same logic as AreaEditPopup.tsx
    const isInSelectedArea = selectedAreas.some((areaId) => {
      const area = areas.find((a) => a.id === areaId);
      if (!area) return false;

      const areaVertices = area.vertices;
      const wallVertices = line.vertices;

      // Check if both wall vertices are in the area vertices
      return wallVertices.every((vertexId: string) =>
        areaVertices.includes(vertexId)
      );
    });

    // Combine wall selection and area selection
    const shouldHighlight = isSelected || isInSelectedArea;

    const thicknessCM = line.properties?.thickness?.length ?? 20;
    const thicknessM = thicknessCM * CM_TO_M;

    const layerAltitude = (line as any).layerAltitude || 0;
    const customHeightM = (line.properties?.height?.length ?? 220) * CM_TO_M;
    const heightM = customHeightM;

    const baseDistance = Math.hypot(vA.x - vB.x, vA.y - vB.y) * CM_TO_M;
    const halfLength = baseDistance / 2;

    // ✅ MITER: Use 2D miter utilities for proper wall joint geometry
    // This handles T-junctions, cross-junctions, and asymmetric corners correctly
    const miterPolygon = computeWallPolygon2D(
      { x: vA.x, y: vA.y },
      { x: vB.x, y: vB.y },
      thicknessCM,
      startMiterInfo,
      endMiterInfo
    );

    // Convert 2D miter points to 3D local-space X offsets
    // In 3D local space: +Z = left side of wall (2D), -Z = right side
    const wallLen = Math.sqrt((vB.x - vA.x) ** 2 + (vB.y - vA.y) ** 2);
    const nDirX = wallLen > 0.001 ? (vB.x - vA.x) / wallLen : 1;
    const nDirY = wallLen > 0.001 ? (vB.y - vA.y) / wallLen : 0;

    // Project miter points onto wall direction to get local X positions
    // Left side in 2D = front face (+Z), Right side = back face (-Z)
    const startFrontX = -halfLength + ((miterPolygon.startLeft.x - vA.x) * nDirX + (miterPolygon.startLeft.y - vA.y) * nDirY) * CM_TO_M;
    const startBackX = -halfLength + ((miterPolygon.startRight.x - vA.x) * nDirX + (miterPolygon.startRight.y - vA.y) * nDirY) * CM_TO_M;
    const endFrontX = halfLength + ((miterPolygon.endLeft.x - vB.x) * nDirX + (miterPolygon.endLeft.y - vB.y) * nDirY) * CM_TO_M;
    const endBackX = halfLength + ((miterPolygon.endRight.x - vB.x) * nDirX + (miterPolygon.endRight.y - vB.y) * nDirY) * CM_TO_M;

    const wallGeometry = createMiteredWallGeometry3DFromCorners(
      baseDistance,
      heightM,
      thicknessM,
      startFrontX,
      startBackX,
      endFrontX,
      endBackX
    );

    // Total distance for UV/material scaling: use the longest face span
    const frontLen = Math.abs(endFrontX - startFrontX);
    const backLen = Math.abs(endBackX - startBackX);
    const totalDistance = Math.max(frontLen, backLen, baseDistance);

    // ✅ GLOBAL UV REMAP: Perfectly synchronize Wall3D texture tiling with 2D WallEditor
    // We rewrite the UVs of the generated geometry so they map to absolute world coordinates.
    // This allows them to perfectly match Konva's global pattern filling.
    const posAttr = wallGeometry.attributes.position;
    const uvAttr = wallGeometry.attributes.uv;

    for (let i = 0; i < posAttr.count; i++) {
      const lx = posAttr.getX(i);
      const ly = posAttr.getY(i);

      let faceStart: { x: number, y: number } | null = null;
      let faceEnd: { x: number, y: number } | null = null;
      let faceMidLocalX = 0;

      // Group 4 (index 0 to 15 in vertices? No, 16 to 19 is front face. 
      // Our geometry adds 24 vertices. 
      // 0-3: Front (+Z), 4-7: Back (-Z), 8-11: Top, 12-15: Bottom, 16-19: Start, 20-23: End.
      if (i >= 0 && i < 4) {
        faceStart = miterPolygon.startLeft;
        faceEnd = miterPolygon.endLeft;
        faceMidLocalX = (startFrontX + endFrontX) / 2;
      } else if (i >= 4 && i < 8) {
        faceStart = miterPolygon.startRight;
        faceEnd = miterPolygon.endRight;
        faceMidLocalX = (startBackX + endBackX) / 2;
      }

      if (faceStart && faceEnd) {
        const faceDx = faceEnd.x - faceStart.x;
        const faceDy = faceEnd.y - faceStart.y;
        const faceLength = Math.max(Math.sqrt(faceDx * faceDx + faceDy * faceDy), 0.001);

        const faceDirX = faceDx / faceLength;
        const faceDirY = faceDy / faceLength;
        const perpX = -faceDirY;
        const perpY = faceDirX;

        const faceCenterX = (faceStart.x + faceEnd.x) / 2;
        const faceCenterY = (faceStart.y + faceEnd.y) / 2;

        const l_x_face = lx - faceMidLocalX;
        const l_y_face = ly;

        // Compute global 2D Konva absolute pixel/world coordinate
        const konvaX = faceCenterX + (l_x_face * 100) * faceDirX + (l_y_face * 100) * perpX;
        const konvaY = faceCenterY + (l_x_face * 100) * faceDirY + (l_y_face * 100) * perpY;

        // Map Konva absolute coordinates into the 0..1 UV format expected by the material repeat multiplier
        uvAttr.setXY(i, (konvaX / 100) / totalDistance, (konvaY / 100) / heightM);
      }
    }
    uvAttr.needsUpdate = true;

    // Create junction fan geometries for 3+ wall junctions
    // These fill the triangular gap between the miter-cut wall end and the junction center
    const halfThicknessM = thicknessM / 2;
    const halfHeightM = heightM / 2;
    let startFanGeo: THREE.BufferGeometry | null = null;
    let endFanGeo: THREE.BufferGeometry | null = null;

    if (startMiterInfo && startMiterInfo.canApplyMiter && startMiterInfo.connectionCount >= 3) {
      startFanGeo = createJunctionFanGeometry(
        startFrontX, startBackX, -halfLength,
        halfThicknessM, halfHeightM
      );
    }
    if (endMiterInfo && endMiterInfo.canApplyMiter && endMiterInfo.connectionCount >= 3) {
      endFanGeo = createJunctionFanGeometry(
        endFrontX, endBackX, halfLength,
        halfThicknessM, halfHeightM
      );
    }

    const wallMesh = new THREE.Mesh(wallGeometry);
    wallMesh.updateMatrix();

    // Convert holes for shader injection
    // ✅ PASS baseDistance (original length) so holes relative to center (0.5) match geometry 0
    const holesForShader = convertHolesToShaderFormat(
      allRelatedHoles, // Use combined holes
      baseDistance,
      heightM
    );

    // Create materials using standard material system
    const { innerMaterial, outerMaterial, topMaterial } = createWallMaterials(
      wallTextures,
      line,
      totalDistance,
      heightM,
      wallIndex,
      shouldHighlight,
      holesForShader
    );

    let materials: THREE.Material[];
    if (wallFacing === "inner-left") {
      materials = [
        innerMaterial,
        innerMaterial,
        topMaterial,
        topMaterial,
        innerMaterial,
        outerMaterial,
      ];
    } else {
      materials = [
        innerMaterial,
        innerMaterial,
        topMaterial,
        topMaterial,
        outerMaterial,
        innerMaterial,
      ];
    }

    wallMesh.material = materials;
    wallMesh.castShadow = true;
    wallMesh.receiveShadow = true;

    const layerAltitudeM = layerAltitude * CM_TO_M;

    // Position and rotation
    const wallAngle = getWallAngle(vA, vB);
    const midX_orig = (vA.x + vB.x) / 2;
    const midZ_orig = (vA.y + vB.y) / 2;

    const position = new THREE.Vector3(
      midX_orig * CM_TO_M,
      layerAltitudeM + heightM / 2,
      midZ_orig * CM_TO_M
    );
    const rotation = new THREE.Euler(0, -wallAngle, 0);
    // --- 🩶 Z-FIGHTING FIX START ---
    // Determine surface normal for this wall
    const normal = new THREE.Vector3(0, 0, 1).applyEuler(rotation).normalize();

    const OFFSET_MM = 0.8; // tiny physical gap

    if (wallFacing === "inner-left") {
      // push slightly toward the polygon interior
      position.addScaledVector(normal, OFFSET_MM / 1000);
    } else if (wallFacing === "inner-right") {
      // push slightly outward
      position.addScaledVector(normal, -OFFSET_MM / 1000);
    }

    // ensure all materials use polygonOffset
    wallMesh.traverse((obj: any) => {
      if (obj.isMesh && Array.isArray(obj.material)) {
        obj.material.forEach((mat: any) => {
          mat.polygonOffset = true;
          mat.polygonOffsetFactor = 2.0;
          mat.polygonOffsetUnits = 2.0;
        });
      } else if (obj.isMesh && obj.material) {
        obj.material.polygonOffset = true;
        obj.material.polygonOffsetFactor = 2.0;
        obj.material.polygonOffsetUnits = 2.0;
      }
    });

    return {
      meshes: [wallMesh, ...(startFanGeo || endFanGeo ? (() => {
        // Hardcoded to match wallMaterialUtils.ts topMaterial color exactly
        const fanMat = new THREE.MeshStandardMaterial({
          color: '#8a8a8a',
          side: THREE.DoubleSide,
        });
        const result: THREE.Mesh[] = [];
        if (startFanGeo) result.push(new THREE.Mesh(startFanGeo, fanMat));
        if (endFanGeo) result.push(new THREE.Mesh(endFanGeo, fanMat));
        return result;
      })() : [])],
      position,
      rotation,
      length: baseDistance,
      height: heightM,
      thickness: thicknessM,
      // ✅ Include materials for transparency mode
      innerMaterial,
      outerMaterial,
      topMaterial,
    };
  }, [
    line,
    vertices,
    holes,
    wallTextures,
    startMiterInfo,
    endMiterInfo,
    wallIndex,
    isSelected,
    selectedAreas,
  ]);

  // ✅ Assign materials to refs for transparency mode
  useEffect(() => {
    if (wallData) {
      innerMaterialRef.current = wallData.innerMaterial;
      outerMaterialRef.current = wallData.outerMaterial;
      topMaterialRef.current = wallData.topMaterial;
    }
  }, [wallData]);

  // ⚡ PERFORMANCE: Frame counter for throttling per-wall useFrame
  // With 50 walls, each running useFrame every frame = 50 * 60 = 3000 calculations/sec
  // Throttling to every 5th frame reduces this to 50 * 12 = 600 calculations/sec
  // Stagger offset spreads work across frames: not all 50 walls compute on frame 0
  // Fade effect is still smooth since opacity changes are gradual (0.1 step)
  const frameCounterRef = useRef(0);

  useFrame(() => {
    // ⚡ THROTTLE: Only run transparency calculations every 5th frame
    // With stagger (wallIndex % 5), 10 walls run per frame instead of 50
    frameCounterRef.current++;
    if ((frameCounterRef.current + wallIndex) % 5 !== 0) return;

    // ⚡ EARLY EXIT: In non-3D modes (360, panorama, render), transparency is always off
    // Skip ALL expensive math (getWorldPosition, getWorldQuaternion, dot products)
    // This saves 600 useless matrix decompositions/sec across 50 walls
    if (!wallTransparenceMode && !wallFadedRef.current) return;

    if (
      !wallData ||
      !wallGroupRef.current ||
      !outerMaterialRef.current ||
      !topMaterialRef.current ||
      !innerMaterialRef.current
    )
      return;
    const outerMaterial = outerMaterialRef.current;
    const topMaterial = topMaterialRef.current;
    const innerMaterial = innerMaterialRef.current;

    let shouldFade = false;

    if (wallTransparenceMode && is3DMode) {
      wallGroupRef.current.getWorldPosition(worldPos);
      wallGroupRef.current.getWorldQuaternion(worldQuat);
      worldNormal.set(0, 0, 1).applyQuaternion(worldQuat).normalize();
      if (wallFacingState.current === "inner-left") {
        worldNormal.negate();
      }

      cameraVec.copy(camera.position).sub(worldPos);
      const signedDistance = cameraVec.dot(worldNormal);
      const perpendicularDistance = Math.abs(signedDistance);
      const lateralDistanceSq = Math.max(
        0,
        cameraVec.lengthSq() - signedDistance * signedDistance
      );
      const lateralDistance = Math.sqrt(lateralDistanceSq);

      const FADE_DIST = is3DMode ? 10 : 10;
      const LATERAL_LIMIT = is3DMode ? 20 : 10;

      shouldFade =
        signedDistance > 0 &&
        perpendicularDistance < FADE_DIST &&
        lateralDistance < LATERAL_LIMIT;
    }

    if (shouldFade === wallFadedRef.current) return;

    // ✅ FIX: Track transparency state change to apply needsUpdate only on toggle
    // This is critical for color-only walls (no texture) because:
    // - Textured materials (via createMaterialFromTextureSet) use `side: DoubleSide`
    //   which makes them render from both sides and transparency works naturally.
    // - Color-only materials use `side: FrontSide` and need shader recompilation
    //   when `transparent` is toggled at runtime — without `needsUpdate`, the
    //   opacity change is silently ignored by Three.js renderer.
    const allMats = [outerMaterial, topMaterial, innerMaterial] as THREE.MeshStandardMaterial[];

    if (shouldFade) {
      allMats.forEach(mat => {
        if (!mat.transparent) {
          // ✅ FIX: Transitioning from opaque → transparent requires needsUpdate
          mat.needsUpdate = true;
        }
        mat.transparent = true;
        mat.opacity = 0.1;
        mat.depthWrite = false;
        // ✅ FIX: Ensure DoubleSide during transparency so both faces render as see-through
        // Color-only walls use FrontSide by default, which makes walls invisible from back
        // instead of transparent. Store original side for restoration.
        if (mat.side !== THREE.DoubleSide) {
          if (!(mat.userData as any).__originalSide) {
            (mat.userData as any).__originalSide = mat.side;
          }
          mat.side = THREE.DoubleSide;
          mat.needsUpdate = true;
        }
      });
    } else {
      allMats.forEach(mat => {
        if (mat.transparent) {
          // ✅ FIX: Transitioning from transparent → opaque requires needsUpdate
          mat.needsUpdate = true;
        }
        mat.opacity = 1;
        mat.transparent = false;
        mat.depthWrite = true;
        // ✅ FIX: Restore original side when transparency is disabled
        if ((mat.userData as any).__originalSide !== undefined) {
          mat.side = (mat.userData as any).__originalSide;
          delete (mat.userData as any).__originalSide;
          mat.needsUpdate = true;
        }
      });
    }

    wallFadedRef.current = shouldFade;
  });

  if (!wallData) return null;

  return (
    <group
      ref={wallGroupRef}
      position={wallData.position}
      rotation={wallData.rotation}
      userData={{ type: "wall", wallId: line.id }}
      name={`wall-${line.id}`}
      onDoubleClick={(e: any) => {
        e.stopPropagation();
        e.nativeEvent?.stopPropagation?.();

        // ✅ FIX: Check if the click was on a hole (door/window) - if so, don't process wall click
        // This prevents selecting the wall when clicking on a door/window
        const intersectedObject = e.object;
        let currentObj = intersectedObject;
        while (currentObj) {
          // Check if this object or any parent has holeId in userData
          if (currentObj.userData?.holeId || currentObj.name?.startsWith('hole-')) {
            // Click was on a hole, don't process as wall click
            return;
          }
          // Check if we hit a wall mesh specifically (not a child hole)
          if (currentObj.userData?.type === 'wall') {
            break; // This is the wall, proceed with wall click
          }
          currentObj = currentObj.parent;
        }

        // Get click position from the event
        const clickPosition = {
          x: e.nativeEvent.clientX,
          y: e.nativeEvent.clientY,
        };

        if (isEditable) {
          onWallClick?.(line.id, clickPosition);
        }
      }}
      onPointerOver={(e: any) => {
        e.stopPropagation();

        // ✅ FIX: Don't show wall hover when hovering over a hole
        const intersectedObject = e.object;
        let currentObj = intersectedObject;
        while (currentObj) {
          if (currentObj.userData?.holeId || currentObj.name?.startsWith('hole-')) {
            return; // Hovering over a hole, don't trigger wall hover
          }
          if (currentObj.userData?.type === 'wall') {
            break;
          }
          currentObj = currentObj.parent;
        }

        // Highlight
        if (wallGroupRef.current && is3DEditMode && isEditable) {
          setHighlight(wallGroupRef.current, true);
        }

        if (isEditable) {
          onLineHover?.(line.id);
        }
      }}
      onPointerOut={(e: any) => {
        e.stopPropagation();

        // Remove highlight
        if (wallGroupRef.current) {
          setHighlight(wallGroupRef.current, false);
        }

        if (isEditable) {
          onLineHover?.(null);
        }
      }}
    >
      {wallData.meshes.map((mesh, i) => (
        <primitive
          key={`${i + 1}-Wall_data_mesh`}
          object={mesh}
          castShadow={false}
          receiveShadow={false}

        />
      ))}


      {holes.map((hole: Hole) => {
        const isSelectedHole = selectedHoles.includes(hole.id);

        return (
          <group key={hole.id}>
            <Hole3D
              hole={hole}
              wallLength={wallData.length}
              wallHeight={wallData.height}
              wallThickness={wallData.thickness}
              models={models}
              isSelected={isSelectedHole}
              onHoleClick={onHoleClick}
              onHoleHover={onHoleHover}
              onHoleUpdate={onHoleUpdate}
              isHovered={hoveredHole === hole.id}
              walkModeActive={walkModeActive}
              isEditable={isEditable}
            />
            {/* Fill the gap created by shader clipping */}
            <HoleFiller
              hole={hole}
              wallLength={wallData.length}
              wallHeight={wallData.height}
              wallThickness={wallData.thickness}
            />
          </group>
        );
      })}
    </group>
  );
};

// ⚡ PERFORMANCE: Custom comparator to prevent unnecessary re-renders  
function arePropsEqual(prevProps: Wall3DProps, nextProps: Wall3DProps): boolean {
  return (
    prevProps.line === nextProps.line &&
    prevProps.vertices === nextProps.vertices &&
    prevProps.holes === nextProps.holes &&
    prevProps.structures === nextProps.structures &&
    prevProps.models === nextProps.models &&
    prevProps.wallTextures === nextProps.wallTextures &&
    prevProps.startMiterInfo === nextProps.startMiterInfo &&
    prevProps.endMiterInfo === nextProps.endMiterInfo &&
    prevProps.wallIndex === nextProps.wallIndex &&
    prevProps.isSelected === nextProps.isSelected &&
    prevProps.selectedAreas === nextProps.selectedAreas &&
    prevProps.selectedHoles === nextProps.selectedHoles &&
    prevProps.hoveredHole === nextProps.hoveredHole &&
    prevProps.hoveredWall === nextProps.hoveredWall &&
    prevProps.areas === nextProps.areas &&
    prevProps.walkModeActive === nextProps.walkModeActive &&
    prevProps.plannerMode === nextProps.plannerMode &&
    prevProps.wallTransparenceMode === nextProps.wallTransparenceMode &&
    prevProps.onWallClick === nextProps.onWallClick &&
    prevProps.onHoleClick === nextProps.onHoleClick &&
    prevProps.onHoleHover === nextProps.onHoleHover &&
    prevProps.onHoleUpdate === nextProps.onHoleUpdate &&
    prevProps.onLineHover === nextProps.onLineHover &&
    prevProps.showAllFloors === nextProps.showAllFloors &&
    prevProps.currentFloorOpacity === nextProps.currentFloorOpacity &&
    prevProps.otherFloorsOpacity === nextProps.otherFloorsOpacity
  );
}

// Create the memoized component with custom comparator
const MemoizedWall3D = React.memo(Wall3D, arePropsEqual);

// ⚡ Track unnecessary re-renders in development
(MemoizedWall3D as any).whyDidYouRender = true;

export default MemoizedWall3D;



// import React, { useEffect, useMemo, useRef } from "react";
// import * as THREE from "three";
// import { CSG } from "three-csg-ts";
// import { useFrame, useThree } from "@react-three/fiber";
// import { CM_TO_M, MODE_360VIEW, MODE_3D } from "../../../../utils/Constants";
// import Hole3D from "./Hole3D";
// import { Hole, Line, Vertex, Area } from "../../../../types/editorModels";
// import {
//   WallTextureData,
//   createMaterialFromTextureSet,
// } from "../../../../utils/assetManager";
// import { ITEM_SELECT_DARK_COLOR } from "../../../../types/types";
// import { useAppSelector } from "../../../../store/hooks";
// import { RootState } from "../../../../store";

// // -------------------------
// // Material cache + helpers
// // -------------------------
// const materialCache = new Map<string, THREE.MeshStandardMaterial>();

// function safeDisposeMaterial(mat?: THREE.Material | THREE.Material[] | null) {
//   if (!mat) return;
//   if (Array.isArray(mat)) {
//     mat.forEach(safeDisposeMaterial);
//     return;
//   }
//   try {
//     const m = mat as THREE.MeshStandardMaterial;
//     if (m.map) {
//       m.map.dispose();
//       // @ts-ignore
//       m.map = undefined;
//     }
//     if (m.normalMap) {
//       m.normalMap.dispose();
//       // @ts-ignore
//       m.normalMap = undefined;
//     }
//     m.dispose();
//   } catch (err) {
//     // swallow
//   }
// }

// function getCachedMaterial(key: string, factory: () => THREE.MeshStandardMaterial) {
//   const existing = materialCache.get(key);
//   if (existing) return existing;
//   const mat = factory();
//   materialCache.set(key, mat);
//   return mat;
// }

// function invalidateMaterial(key: string) {
//   const m = materialCache.get(key);
//   if (!m) return;
//   safeDisposeMaterial(m);
//   materialCache.delete(key);
// }

// // -------------------------
// // Utility helpers
// // -------------------------
// const TARGET_TEXTURE_HEIGHT = 2.4;
// function getWallAngle(v1: Vertex, v2: Vertex) {
//   return Math.atan2(v2.y - v1.y, v2.x - v1.x);
// }
// function isPointInPolygon(point: THREE.Vector2, polygon: THREE.Vector2[]) {
//   let inside = false;
//   for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
//     const xi = polygon[i].x,
//       yi = polygon[i].y;
//     const xj = polygon[j].x,
//       yj = polygon[j].y;
//     const intersect = yi > point.y !== yj > point.y && point.x < ((xj - xi) * (point.y - yi)) / (yj - yi) + xi;
//     if (intersect) inside = !inside;
//   }
//   return inside;
// }
// function getWallFacingDirection(line: Line, verticesMap: Map<string, Vertex>, areas: Area[]) {
//   const [vA, vB] = line.vertices.map((id) => verticesMap.get(id));
//   if (!vA || !vB) return null;
//   const area = areas.find((a) => line.vertices.every((vId) => a.vertices.includes(vId)));
//   if (!area) return null;
//   const polygon = area.vertices.map((vId) => verticesMap.get(vId)).filter((v): v is Vertex => !!v).map((v) => new THREE.Vector2(v.x, v.y));
//   if (polygon.length < 3) return null;
//   const dir = new THREE.Vector2(vB.x - vA.x, vB.y - vA.y).normalize();
//   const leftNormal = new THREE.Vector2(-dir.y, dir.x);
//   const rightNormal = new THREE.Vector2(dir.y, -dir.x);
//   const mid = new THREE.Vector2((vA.x + vB.x) / 2, (vA.y + vB.y) / 2);
//   const leftSample = mid.clone().addScaledVector(leftNormal, 5);
//   const rightSample = mid.clone().addScaledVector(rightNormal, 5);
//   const leftInside = isPointInPolygon(leftSample, polygon);
//   const rightInside = isPointInPolygon(rightSample, polygon);
//   if (leftInside && !rightInside) return "inner-left" as const;
//   if (rightInside && !leftInside) return "inner-right" as const;
//   return "inner-left" as const;
// }

// function getCornerExtension(v_curr: Vertex, v_prev: Vertex, v_next: Vertex, thickness: number) {
//   const v1 = new THREE.Vector2(v_prev.x - v_curr.x, v_prev.y - v_curr.y).normalize();
//   const v2 = new THREE.Vector2(v_next.x - v_curr.x, v_next.y - v_curr.y).normalize();
//   const dot = Math.max(-1, Math.min(1, v1.dot(v2)));
//   const angle = Math.acos(dot);
//   if (Math.abs(angle - Math.PI / 2) < 0.001) return thickness / 2;
//   const cornerAngle = angle / 2;
//   const extension = Math.abs(thickness / 2 / Math.tan(cornerAngle));
//   return Math.max(thickness / 10, extension);
// }

// // -------------------------
// // Component
// // -------------------------
// type Wall3DProps = {
//   line: Line;
//   vertices: Vertex[];
//   holes: Hole[];
//   allLines: Line[];
//   allHoles: Hole[];
//   models: Map<string, THREE.Group>;
//   wallTextures: WallTextureData | null;
//   prevVertex?: Vertex | null;
//   nextVertex?: Vertex | null;
//   wallIndex: number;
//   isSelected?: boolean;
//   selectedAreas?: string[];
//   selectedHoles?: string[];
//   hoveredHole?: string | null;
//   hoveredWall?: string | null;
//   areas?: Area[];
//   onWallClick?: (wallId: string, clickPosition?: { x: number; y: number }) => void;
//   onHoleClick?: (holeId: string, clickPosition?: { x: number; y: number }) => void;
//   onHoleHover?: (holeId: string | null) => void;
//   onLineHover?: (LineId: string | null) => void;
//   walkModeActive?: boolean;
// };

// const Wall3D: React.FC<Wall3DProps> = ({
//   line,
//   vertices = [],
//   holes,
//   allLines = [],
//   allHoles = [],
//   models,
//   wallTextures,
//   prevVertex,
//   nextVertex,
//   wallIndex,
//   isSelected = false,
//   selectedAreas = [],
//   selectedHoles = [],
//   hoveredHole = null,
//   areas = [],
//   onWallClick,
//   onHoleClick,
//   onHoleHover,
//   onLineHover,
//   walkModeActive = false,
// }) => {
//   const { camera } = useThree();
//   const plannerMode = useAppSelector((s: RootState) => s.planner.mode);

//   // temp refs for per-frame computations
//   const tmpWorldPos = useRef(new THREE.Vector3());
//   const tmpWorldQuat = useRef(new THREE.Quaternion());
//   const tmpWorldNormal = useRef(new THREE.Vector3());
//   const tmpCameraVec = useRef(new THREE.Vector3());

//   const verticesMap = useMemo(() => new Map(vertices.map((v) => [v.id, v])), [vertices]);

//   const wallFacing = useMemo(() => getWallFacingDirection(line, verticesMap, areas), [line, verticesMap, areas]);

//   // --- compute heavy geometry + CSG once when inputs change ---
//   const wallData = useMemo(() => {
//     const vA = verticesMap.get(line.vertices[0]);
//     const vB = verticesMap.get(line.vertices[1]);
//     if (!vA || !vB) return null;

//     const isInSelectedArea = selectedAreas.some((areaId) => {
//       const area = areas.find((a) => a.id === areaId);
//       if (!area) return false;
//       return line.vertices.every((vertexId) => area.vertices.includes(vertexId));
//     });

//     const shouldHighlight = isSelected || isInSelectedArea;

//     const thicknessM = (line.properties?.thickness?.length ?? 20) * CM_TO_M;
//     const heightM = (line.properties?.height?.length ?? 220) * CM_TO_M;
//     const baseDistance = Math.hypot(vA.x - vB.x, vA.y - vB.y) * CM_TO_M;

//     let extensionA = 0;
//     if (prevVertex) extensionA = getCornerExtension(vA, prevVertex, vB, thicknessM);
//     let extensionB = 0;
//     if (nextVertex) extensionB = getCornerExtension(vB, vA, nextVertex, thicknessM);
//     const totalDistance = baseDistance + extensionA + extensionB;

//     const wallGeometry = new THREE.BoxGeometry(Math.max(0.001, totalDistance - 0.02), heightM, thicknessM);
//     const wallMesh = new THREE.Mesh(wallGeometry);
//     wallMesh.updateMatrix();

//     let wallCSG = CSG.fromMesh(wallMesh);

//     // find overlapping wall holes (light-weight inline replica)
//     const extraHoles: Hole[] = (() => {
//       const [wa, wb] = line.vertices.map((id) => verticesMap.get(id));
//       if (!wa || !wb) return [];
//       const dir1 = new THREE.Vector2(wb.x - wa.x, wb.y - wa.y);
//       const len1 = dir1.length();
//       if (len1 < 0.001) return [];
//       dir1.normalize();
//       const overlapping = allLines.find((w) => {
//         if (w.id === line.id) return false;
//         const [oa, ob] = w.vertices.map((id) => verticesMap.get(id));
//         if (!oa || !ob) return false;
//         const dir2 = new THREE.Vector2(ob.x - oa.x, ob.y - oa.y);
//         const len2 = dir2.length();
//         if (len2 < 0.001) return false;
//         dir2.normalize();
//         if (Math.abs(dir1.dot(dir2)) < 0.9999) return false;
//         const distToLine = Math.abs((wb.y - wa.y) * oa.x - (wb.x - wa.x) * oa.y + wb.x * wa.y - wb.y * wa.x) / len1;
//         const MAX_DISTANCE_CM = line.properties?.thickness?.length ?? 5;
//         if (distToLine > MAX_DISTANCE_CM) return false;
//         const project = (base: Vertex, dir: THREE.Vector2, v: Vertex) => dir.dot(new THREE.Vector2(v.x - base.x, v.y - base.y));
//         const a0 = 0,
//           a1 = len1;
//         const b0 = project(wa, dir1, oa),
//           b1 = project(wa, dir1, ob);
//         const minA = Math.min(a0, a1),
//           maxA = Math.max(a0, a1);
//         const minB = Math.min(b0, b1),
//           maxB = Math.max(b0, b1);
//         return Math.min(maxA, maxB) - Math.max(minA, minB) > 0.001;
//       });
//       if (!overlapping) return [];
//       const overlapHoles = allHoles.filter((h) => h.line === overlapping.id);
//       if (!overlapHoles.length) return [];
//       const [o1, o2] = overlapping.vertices.map((id) => verticesMap.get(id));
//       if (!o1 || !o2) return [];
//       const dir2 = new THREE.Vector2(o2.x - o1.x, o2.y - o1.y).normalize();
//       const len2 = Math.hypot(o2.x - o1.x, o2.y - o1.y);
//       const mirrored: Hole[] = [];
//       for (const hole of overlapHoles) {
//         const offsetDistance = (hole.offset ?? 0) * len2;
//         const holePos2D = new THREE.Vector2(o1.x, o1.y).addScaledVector(dir2, offsetDistance);
//         const relativeOffset = new THREE.Vector2(holePos2D.x - wa.x, holePos2D.y - wa.y);
//         const projection = relativeOffset.dot(dir1);
//         const offsetRatio = projection / len1;
//         if (offsetRatio >= 0 && offsetRatio <= 1) {
//           mirrored.push({ ...hole, id: `${hole.id}_mirror_${line.id}`, line: line.id, offset: offsetRatio });
//         }
//       }
//       return mirrored;
//     })();

//     const allHolesForThisWall = [...holes, ...extraHoles];

//     allHolesForThisWall.forEach((hole) => {
//       const w = (hole.properties?.width?.length ?? 100) * CM_TO_M;
//       const h = (hole.properties?.height?.length ?? 200) * CM_TO_M;
//       const alt = (hole.properties?.altitude?.length ?? 0) * CM_TO_M;
//       const d = thicknessM * 1.5;
//       const holeGeom = new THREE.BoxGeometry(Math.max(0.001, w), Math.max(0.001, h), Math.max(0.001, d));
//       const holeMesh = new THREE.Mesh(holeGeom);
//       const holeOffsetFromCenter = (hole.offset ?? 0.5) * baseDistance - baseDistance / 2;
//       holeMesh.position.x = holeOffsetFromCenter + (extensionA - extensionB) / 2;
//       holeMesh.position.y = alt + h / 2 - heightM / 2;
//       holeMesh.position.z = 0;
//       holeMesh.updateMatrix();
//       const holeCSG = CSG.fromMesh(holeMesh);
//       wallCSG = wallCSG.subtract(holeCSG);
//       holeGeom.dispose();
//     });

//     const finalMesh = CSG.toMesh(wallCSG, wallMesh.matrix);

//     const borderGeom = new THREE.BoxGeometry(totalDistance, 0.01, thicknessM);
//     const borderMesh = new THREE.Mesh(borderGeom);

//     const wallAngle = getWallAngle(vA, vB);
//     const wallDirection = new THREE.Vector2(vB.x - vA.x, vB.y - vA.y).normalize();
//     const midX_orig = (vA.x + vB.x) / 2;
//     const midZ_orig = (vA.y + vB.y) / 2;
//     const shift = (extensionA - extensionB) / 2;
//     const shiftX = wallDirection.x * shift;
//     const shiftZ = -wallDirection.y * shift;
//     const position = new THREE.Vector3(midX_orig * CM_TO_M + shiftX, heightM / 2, midZ_orig * CM_TO_M + shiftZ);
//     const rotation = new THREE.Euler(0, -wallAngle, 0);

//     const normal = new THREE.Vector3(0, 0, 1).applyEuler(rotation).normalize();
//     const OFFSET_MM = 0.8;
//     if (wallFacing === "inner-left") position.addScaledVector(normal, OFFSET_MM / 1000);
//     else if (wallFacing === "inner-right") position.addScaledVector(normal, -OFFSET_MM / 1000);

//     // assign placeholders — will be replaced by materials in effect below
//     finalMesh.material = new THREE.MeshStandardMaterial();
//     borderMesh.material = new THREE.MeshStandardMaterial();

//     return {
//       meshes: [finalMesh],
//       border: borderMesh,
//       position,
//       rotation,
//       length: baseDistance,
//       height: heightM,
//       shouldHighlight,
//     };
//   }, [line, vertices, holes, allLines, allHoles, prevVertex, nextVertex, wallIndex, isSelected, selectedAreas, areas, wallFacing]);

//   // --- create/update materials when wallData or wallTextures change ---
//   const prevTexRef = useRef<WallTextureData | null>(null);
//   useEffect(() => {
//     if (!wallData) return;

//     // Compute cache keys (wall-level keys — simpler & shareable)
//     const innerKey = `${line.id}-inner`;
//     const outerKey = `${line.id}-outer`;
//     const topKey = `${line.id}-top`;

//     // If wallTextures changed (reference or content), invalidate so new textures load
//     const texChanged = prevTexRef.current !== wallTextures;
//     if (texChanged) {
//       invalidateMaterial(innerKey);
//       invalidateMaterial(outerKey);
//       invalidateMaterial(topKey);
//       prevTexRef.current = wallTextures;
//     }

//     // Factories — use texture set when available, otherwise fallback color
//     const makeInner = () => {
//       if (wallTextures?.inner) {
//         const mat = createMaterialFromTextureSet(wallTextures.inner, line.asset_urls?.inner?.fallback_color || "#9b9999");
//         return mat;
//       }
//       const colorHex = (line.asset_urls?.inner?.fallback_color || "#9b9999").slice(0, 7);
//       return new THREE.MeshStandardMaterial({ color: colorHex, roughness: 1, metalness: 1 });
//     };
//     const makeOuter = () => {
//       if (wallTextures?.outer) {
//         const mat = createMaterialFromTextureSet(wallTextures.outer, line.asset_urls?.outer?.fallback_color || "#9b9999");
//         return mat;
//       }
//       const colorHex = (line.asset_urls?.outer?.fallback_color || "#9b9999").slice(0, 7);
//       return new THREE.MeshStandardMaterial({ color: colorHex, roughness: 1, metalness: 1 });
//     };
//     const makeTop = () => new THREE.MeshStandardMaterial({ color: "#8a8a8a", roughness: 0.9 });

//     const innerMat = getCachedMaterial(innerKey, makeInner);
//     const outerMat = getCachedMaterial(outerKey, makeOuter);
//     const topMat = getCachedMaterial(topKey, makeTop);

//     // Apply selection highlight to top
//     if (wallData.shouldHighlight) {
//       topMat.color = new THREE.Color(ITEM_SELECT_DARK_COLOR);
//       topMat.emissive = new THREE.Color(ITEM_SELECT_DARK_COLOR).multiplyScalar(0.2);
//     }

//     // configure repeats safely
//     const configureRepeat = (material: THREE.MeshStandardMaterial | undefined, side: "inner" | "outer") => {
//       if (!material) return;
//       try {
//         const map = material.map;
//         if (map) {
//           map.wrapS = THREE.RepeatWrapping;
//           map.wrapT = THREE.RepeatWrapping;
//           const totalDistance = (wallData.length ?? 1);
//           const heightM = (wallData.height ?? 2.2);
//           const baseUScale = Math.max(1, totalDistance / TARGET_TEXTURE_HEIGHT);
//           const baseVScale = Math.max(1, heightM / TARGET_TEXTURE_HEIGHT);
//           const storedScaleX = (line.asset_urls?.[side]?.texture_scale_x as number) || 1;
//           const storedScaleY = (line.asset_urls?.[side]?.texture_scale_y as number) || 1;
//           map.repeat.set(baseUScale / storedScaleX, baseVScale / storedScaleY);
//           map.needsUpdate = true;
//           map.minFilter = THREE.LinearFilter;
//           map.magFilter = THREE.LinearFilter;
//         }
//         if (material.normalMap && material.map) {
//           material.normalMap.wrapS = THREE.RepeatWrapping;
//           material.normalMap.wrapT = THREE.RepeatWrapping;
//           // @ts-ignore
//           material.normalMap.repeat.copy(material.map.repeat);
//           material.normalMap.minFilter = THREE.LinearFilter;
//           material.normalMap.magFilter = THREE.LinearFilter;
//           material.normalMap.needsUpdate = true;
//         }
//       } catch (err) {
//         // ignore
//       }
//     };

//     configureRepeat(innerMat, "inner");
//     configureRepeat(outerMat, "outer");

//     // Attach materials to final mesh
//     const finalMesh = wallData.meshes[0];
//     const materialsArr = wallFacing === "inner-left"
//       ? [innerMat, innerMat, innerMat, innerMat, innerMat, outerMat]
//       : [innerMat, innerMat, innerMat, innerMat, outerMat, innerMat];

//     if (finalMesh && (finalMesh as any).isMesh) {
//       // Avoid disposing cached materials - only replace placeholder
//       try {
//         // If placeholder materials exist, avoid disposing cached ones
//         // set material array
//         (finalMesh as any).material = materialsArr as any;
//         (finalMesh as any).material.forEach((mat: any) => {
//           mat.polygonOffset = true;
//           mat.polygonOffsetFactor = 1.0;
//           mat.polygonOffsetUnits = 1.0;
//         });
//       } catch (err) {
//         // ignore
//       }
//     }

//     if (wallData.border) {
//       try {
//         // wallData.border.material = topMat;
//       } catch {}
//     }

//     // cleanup: nothing to dispose here (materials are cached)
//     return () => {};
//   }, [wallData, wallTextures, line, line.asset_urls, wallFacing]);

//   // Dispose geometry when wallData changes/unmounts
//   useEffect(() => {
//     return () => {
//       if (!wallData) return;
//       try {
//         wallData.meshes.forEach((m: any) => {
//           if (m.geometry) m.geometry.dispose();
//           // do not dispose material because materials may be cached/shared
//         });
//         if (wallData.border && wallData.border.geometry) wallData.border.geometry.dispose();
//       } catch (err) {
//         // ignore
//       }
//     };
//   }, [wallData]);

//   // lightweight per-frame fade logic
//   useFrame(() => {
//     if (!wallData) return;
//     const finalMesh = wallData.meshes[0] as THREE.Mesh | undefined;
//     if (!finalMesh) return;
//     const worldPos = tmpWorldPos.current;
//     const worldQuat = tmpWorldQuat.current;
//     const worldNormal = tmpWorldNormal.current;
//     const cameraVec = tmpCameraVec.current;

//     finalMesh.getWorldPosition(worldPos);
//     finalMesh.getWorldQuaternion(worldQuat);
//     worldNormal.set(0, 0, 1).applyQuaternion(worldQuat).normalize();
//     if (wallFacing === "inner-left") worldNormal.negate();

//     cameraVec.copy(camera.position).sub(worldPos);
//     const signedDistance = cameraVec.dot(worldNormal);
//     const perpendicularDistance = Math.abs(signedDistance);
//     const lateralDistanceSq = Math.max(0, cameraVec.lengthSq() - signedDistance * signedDistance);
//     const lateralDistance = Math.sqrt(lateralDistanceSq);

//     const FADE_DIST = plannerMode === MODE_3D ? 10 : 10;
//     const LATERAL_LIMIT = plannerMode === MODE_3D ? 20 : 20;
//     const shouldFade = signedDistance > 0 && perpendicularDistance < FADE_DIST && lateralDistance < LATERAL_LIMIT && (plannerMode === MODE_3D || plannerMode === MODE_360VIEW);

//     const mats = Array.isArray(finalMesh.material) ? finalMesh.material : [finalMesh.material];
//     mats.forEach((mat: any) => {
//       if (!mat) return;
//           if (shouldFade) {
//       mat.transparent = true;
//       mat.opacity = 0.1;
//       mat.depthWrite = false;
//     } else {
//       mat.opacity = 1;
//       mat.transparent = false;
//       mat.depthWrite = true;
//     }
//     });
//   });

//   if (!wallData) return null;

//   return (
//     <group
//       position={wallData.position}
//       rotation={wallData.rotation}
//       userData={{ type: "wall", wallId: line.id }}
//       name={`wall-${line.id}`}
//       onDoubleClick={(e: any) => {
//         e.stopPropagation();
//         e.nativeEvent?.stopPropagation();
//         const clickPosition = { x: e.nativeEvent.clientX, y: e.nativeEvent.clientY };
//         onWallClick?.(line.id, clickPosition);
//       }}
//       onPointerOver={(e: any) => {
//         e.stopPropagation();
//         onLineHover?.(line.id);
//       }}
//       onPointerOut={(e: any) => {
//         e.stopPropagation();
//         onLineHover?.(null);
//       }}
//     >
//       {wallData.meshes.map((mesh, i) => (
//         <primitive key={`${line.id}-mesh-${i}`} object={mesh} castShadow={false} receiveShadow={false} />
//       ))}

//       {wallData.border && (
//         <primitive key={`${line.id}-border`} object={wallData.border} position={[0, wallData.height / 2 + 0.015, 0]} castShadow={false} receiveShadow={false} />
//       )}

//       {holes.map((hole) => {
//         const isSelectedHole = selectedHoles.includes(hole.id);
//         return (
//           <Hole3D key={hole.id} hole={hole} wallLength={wallData.length} wallHeight={wallData.height} models={models} isSelected={isSelectedHole} onHoleClick={onHoleClick} onHoleHover={onHoleHover} isHovered={hoveredHole === hole.id} walkModeActive={walkModeActive} />
//         );
//       })}
//     </group>
//   );
// };

// export default Wall3D;

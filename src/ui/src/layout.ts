import type { Edge, Node } from '@xyflow/react';
import ELK from 'elkjs/lib/elk.bundled.js';
import { sourcePortHandleId, targetPortHandleId } from './portHandles';
import { normalizePortPlacements } from './portPlacement';
import type { NodeContext, PartInstance, PortPlacement, PortSide, SysmlConnection } from './types';

const elk = new ELK();
const nodeWidth = 360;
const minNodeHeight = 150;

type ElkLayoutNode = {
  id: string;
  width?: number;
  height?: number;
  x?: number;
  y?: number;
  children?: ElkLayoutNode[];
  edges?: Array<{
    id: string;
    sources: string[];
    targets: string[];
  }>;
  layoutOptions?: Record<string, string>;
};

export async function layoutElements<TNode extends Record<string, unknown>, TEdge extends Record<string, unknown>>(
  nodes: Array<Node<TNode>>,
  edges: Array<Edge<TEdge>>,
): Promise<{ nodes: Array<Node<TNode>>; edges: Array<Edge<TEdge>> }> {
  const layoutedNodes = await layoutByContext(nodes, edges);
  const orientedNodes = orientPortsTowardConnections(layoutedNodes, edges);
  const orientedEdges = updateEdgeHandles(orientedNodes, edges);

  return { nodes: orientedNodes, edges: orientedEdges };
}

async function layoutFlat<TNode extends Record<string, unknown>, TEdge extends Record<string, unknown>>(
  nodes: Array<Node<TNode>>,
  edges: Array<Edge<TEdge>>,
): Promise<Array<Node<TNode>>> {
  const graph: ElkLayoutNode = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.padding': '[top=90,left=90,bottom=90,right=90]',
      'elk.spacing.nodeNode': '155',
      'elk.spacing.edgeEdge': '42',
      'elk.spacing.edgeNode': '72',
      'elk.layered.spacing.nodeNodeBetweenLayers': '270',
      'elk.layered.spacing.edgeNodeBetweenLayers': '72',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
    },
    children: nodes.map((node) => ({
      id: node.id,
      ...getLayoutSize(node),
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  };

  const layouted = (await elk.layout(graph)) as ElkLayoutNode;
  const positions = new Map(layouted.children?.map((node) => [node.id, node]) ?? []);

  return nodes.map((node) => {
    const layoutedNode = positions.get(node.id);
    return {
      ...node,
      position: {
        x: layoutedNode?.x ?? node.position.x,
        y: layoutedNode?.y ?? node.position.y,
      },
    };
  });
}

async function layoutByContext<TNode extends Record<string, unknown>, TEdge extends Record<string, unknown>>(
  nodes: Array<Node<TNode>>,
  edges: Array<Edge<TEdge>>,
) {
  const groups = new Map<string, Array<Node<TNode>>>();

  for (const node of nodes) {
    const context = getContext(node);
    groups.set(context.id, [...(groups.get(context.id) ?? []), node]);
  }

  const groupLayouts = await Promise.all(
    [...groups.entries()].map(async ([contextId, groupNodes]) => {
      const nodeIds = new Set(groupNodes.map((node) => node.id));
      const internalEdges = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
      const layouted = await layoutFlat(groupNodes, internalEdges);
      const bounds = getBounds(layouted);
      const context = getContext(groupNodes[0]);

      return {
        contextId,
        context,
        nodes: layouted,
        width: bounds.width,
        height: bounds.height,
        minX: bounds.minX,
        minY: bounds.minY,
      };
    }),
  );

  const columnWidths = new Map<number, number>();
  const rowHeights = new Map<number, number>();
  for (const group of groupLayouts) {
    columnWidths.set(group.context.column, Math.max(columnWidths.get(group.context.column) ?? 0, group.width));
    rowHeights.set(group.context.row, Math.max(rowHeights.get(group.context.row) ?? 0, group.height));
  }

  const columnOffsets = offsets(columnWidths, 360);
  const rowOffsets = offsets(rowHeights, 280);

  return groupLayouts.flatMap((group) => {
    const groupX = columnOffsets.get(group.context.column) ?? 0;
    const groupY = rowOffsets.get(group.context.row) ?? 0;

    return group.nodes.map((node) => ({
      ...node,
      position: {
        x: node.position.x - group.minX + groupX,
        y: node.position.y - group.minY + groupY,
      },
    }));
  });
}

export function getNodeSize(instance?: PartInstance) {
  const definition = instance?.definition;
  const attributes = definition?.attributes.length ?? 0;
  const ports = definition?.ports.length ?? 0;
  const actions = definition?.actions.length ?? 0;
  const states = definition?.states.length ?? 0;
  const compartmentCount = [attributes, ports, actions, states].filter((count) => count > 0).length;
  const rowCount = attributes + ports + actions + states;

  return {
    width: nodeWidth,
    height: Math.max(minNodeHeight, 64 + compartmentCount * 28 + rowCount * 16),
  };
}

function orientPortsTowardConnections<TNode extends Record<string, unknown>, TEdge extends Record<string, unknown>>(
  nodes: Array<Node<TNode>>,
  edges: Array<Edge<TEdge>>,
) {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const accumulators = new Map<string, { x: number; y: number; count: number }>();

  for (const edge of edges) {
    const connection = edge.data?.connection as SysmlConnection | undefined;
    if (!connection) {
      continue;
    }

    addPortVector(
      accumulators,
      `${connection.sourcePart}:${connection.sourcePort}`,
      nodesById.get(connection.sourcePart),
      nodesById.get(connection.targetPart),
    );
    addPortVector(
      accumulators,
      `${connection.targetPart}:${connection.targetPort}`,
      nodesById.get(connection.targetPart),
      nodesById.get(connection.sourcePart),
    );
  }

  return nodes.map((node) => {
    const data = node.data as {
      instance?: PartInstance;
      portPlacements?: Record<string, PortPlacement>;
      lockedPortPlacements?: Record<string, boolean>;
    };
    const ports = data.instance?.definition?.ports ?? [];
    const nextPlacements = { ...(data.portPlacements ?? {}) };
    const automaticPortsBySide: Record<PortSide, Array<{ name: string; score: number }>> = {
      left: [],
      right: [],
      top: [],
      bottom: [],
    };

    for (const port of ports) {
      if (data.lockedPortPlacements?.[port.name]) {
        continue;
      }

      const vector = accumulators.get(`${node.id}:${port.name}`);
      if (!vector || vector.count === 0) {
        continue;
      }

      const placement = vectorToPlacement(vector.x / vector.count, vector.y / vector.count);
      nextPlacements[port.name] = placement;
      automaticPortsBySide[placement.side].push({
        name: port.name,
        score: sideSortScore(placement.side, vector.x / vector.count, vector.y / vector.count),
      });
    }

    for (const [side, sidePorts] of Object.entries(automaticPortsBySide) as Array<
      [PortSide, Array<{ name: string; score: number }>]
    >) {
      sidePorts
        .sort((a, b) => a.score - b.score || a.name.localeCompare(b.name))
        .forEach((port, index) => {
          nextPlacements[port.name] = {
            side,
            offset: (index + 1) / (sidePorts.length + 1),
          };
        });
    }

    return {
      ...node,
      data: {
        ...node.data,
        portPlacements: normalizePortPlacements(nextPlacements),
      },
    };
  });
}

function updateEdgeHandles<TNode extends Record<string, unknown>, TEdge extends Record<string, unknown>>(
  nodes: Array<Node<TNode>>,
  edges: Array<Edge<TEdge>>,
) {
  const placementsByNode = new Map(
    nodes.map((node) => [
      node.id,
      (node.data as { portPlacements?: Record<string, PortPlacement> }).portPlacements ?? {},
    ]),
  );

  return edges.map((edge) => {
    const connection = edge.data?.connection as SysmlConnection | undefined;
    if (!connection) {
      return edge;
    }

    return {
      ...edge,
      sourceHandle: sourcePortHandleId(
        connection.sourcePort,
        placementsByNode.get(connection.sourcePart)?.[connection.sourcePort]?.side ?? 'right',
      ),
      targetHandle: targetPortHandleId(
        connection.targetPort,
        placementsByNode.get(connection.targetPart)?.[connection.targetPort]?.side ?? 'left',
      ),
    };
  });
}

function getLayoutSize<TNode extends Record<string, unknown>>(node: Node<TNode>) {
  // Prefer the size React Flow measured from the rendered DOM, so the layout
  // reflects the box's real (content-driven) dimensions. Fall back to an explicit
  // style size, then to the pre-render estimate for the very first pass.
  const measured = node.measured;
  if (measured?.width && measured?.height) {
    return { width: measured.width, height: measured.height };
  }

  const width = typeof node.style?.width === 'number' ? node.style.width : undefined;
  const height = typeof node.style?.height === 'number' ? node.style.height : undefined;
  if (width && height) {
    return { width, height };
  }

  const instance = node.data.instance as PartInstance | undefined;
  return getNodeSize(instance);
}

function getContext<TNode extends Record<string, unknown>>(node: Node<TNode>): NodeContext {
  return node.data.context as NodeContext;
}

function getBounds<TNode extends Record<string, unknown>>(nodes: Array<Node<TNode>>) {
  const extents = nodes.reduce(
    (bounds, node) => {
      const size = getLayoutSize(node);
      return {
        minX: Math.min(bounds.minX, node.position.x),
        minY: Math.min(bounds.minY, node.position.y),
        maxX: Math.max(bounds.maxX, node.position.x + size.width),
        maxY: Math.max(bounds.maxY, node.position.y + size.height),
      };
    },
    { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity },
  );

  return {
    ...extents,
    width: Math.max(1, extents.maxX - extents.minX),
    height: Math.max(1, extents.maxY - extents.minY),
  };
}

function offsets(sizes: Map<number, number>, spacing: number) {
  const result = new Map<number, number>();
  let cursor = 0;

  for (const key of [...sizes.keys()].sort((a, b) => a - b)) {
    result.set(key, cursor);
    cursor += (sizes.get(key) ?? 0) + spacing;
  }

  return result;
}

function addPortVector<TNode extends Record<string, unknown>>(
  accumulators: Map<string, { x: number; y: number; count: number }>,
  key: string,
  self?: Node<TNode>,
  other?: Node<TNode>,
) {
  if (!self || !other) {
    return;
  }

  const selfCenter = centerOf(self);
  const otherCenter = centerOf(other);
  const current = accumulators.get(key) ?? { x: 0, y: 0, count: 0 };
  current.x += otherCenter.x - selfCenter.x;
  current.y += otherCenter.y - selfCenter.y;
  current.count += 1;
  accumulators.set(key, current);
}

function centerOf<TNode extends Record<string, unknown>>(node: Node<TNode>) {
  const size = getLayoutSize(node);
  return {
    x: node.position.x + size.width / 2,
    y: node.position.y + size.height / 2,
  };
}

function vectorToPlacement(x: number, y: number): PortPlacement {
  if (Math.abs(x) >= Math.abs(y)) {
    return {
      side: x >= 0 ? 'right' : 'left',
      offset: clamp(0.5 + y / 700),
    };
  }

  return {
    side: y >= 0 ? 'bottom' : 'top',
    offset: clamp(0.5 + x / 1000),
  };
}

function sideSortScore(side: PortSide, x: number, y: number) {
  if (side === 'left' || side === 'right') {
    return y;
  }

  return x;
}

function clamp(value: number) {
  return Math.min(0.94, Math.max(0.06, value));
}

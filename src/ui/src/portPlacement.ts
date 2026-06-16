import type { PartInstance, PortDirection, PortPlacement, PortSide, SysmlConnection } from './types';

type PortStats = {
  sourceCount: number;
  targetCount: number;
};

const sideOrder: PortSide[] = ['left', 'right', 'top', 'bottom'];
const minimumPortGap = 0.075;
const minimumOffset = 0.06;
const maximumOffset = 0.94;

export function getEffectiveDirection(direction?: PortDirection): PortDirection {
  return direction ?? 'both';
}

export function placePorts(instance: PartInstance, connections: SysmlConnection[]): Record<string, PortPlacement> {
  const ports = instance.definition?.ports ?? [];
  const stats = new Map<string, PortStats>();

  for (const port of ports) {
    stats.set(port.name, { sourceCount: 0, targetCount: 0 });
  }

  for (const connection of connections) {
    if (connection.sourcePart === instance.name) {
      const stat = stats.get(connection.sourcePort);
      if (stat) {
        stat.sourceCount += 1;
      }
    }
    if (connection.targetPart === instance.name) {
      const stat = stats.get(connection.targetPort);
      if (stat) {
        stat.targetCount += 1;
      }
    }
  }

  const loads: Record<PortSide, number> = {
    left: 0,
    right: 0,
    top: 0,
    bottom: 0,
  };
  const sidesByPort: Record<string, PortSide> = {};

  const sortedPorts = [...ports].sort((a, b) => {
    const aStats = stats.get(a.name);
    const bStats = stats.get(b.name);
    const aDegree = (aStats?.sourceCount ?? 0) + (aStats?.targetCount ?? 0);
    const bDegree = (bStats?.sourceCount ?? 0) + (bStats?.targetCount ?? 0);
    return bDegree - aDegree || a.name.localeCompare(b.name);
  });

  for (const port of sortedPorts) {
    const side = chooseSide(getEffectiveDirection(port.direction), stats.get(port.name), loads, port.name);
    sidesByPort[port.name] = side;
    loads[side] += 1;
  }

  const placements: Record<string, PortPlacement> = {};
  for (const side of sideOrder) {
    const sidePorts = sortedPorts.filter((port) => sidesByPort[port.name] === side);
    sidePorts.forEach((port, index) => {
      placements[port.name] = {
        side,
        offset: (index + 1) / (sidePorts.length + 1),
      };
    });
  }

  return placements;
}

export function normalizePortPlacements(placements: Record<string, PortPlacement>): Record<string, PortPlacement> {
  const normalized = { ...placements };

  for (const side of sideOrder) {
    const sidePorts = Object.entries(normalized)
      .filter(([, placement]) => placement.side === side)
      .sort((a, b) => a[1].offset - b[1].offset || a[0].localeCompare(b[0]));

    if (!hasStacking(sidePorts)) {
      continue;
    }

    sidePorts.forEach(([portName], index) => {
      normalized[portName] = {
        side,
        offset: spreadOffset(index, sidePorts.length),
      };
    });
  }

  return normalized;
}

export function getStackedPortGroups(
  placements: Record<string, PortPlacement>,
): Array<{ side: PortSide; ports: string[] }> {
  const groups: Array<{ side: PortSide; ports: string[] }> = [];

  for (const side of sideOrder) {
    const sidePorts = Object.entries(placements)
      .filter(([, placement]) => placement.side === side)
      .sort((a, b) => a[1].offset - b[1].offset || a[0].localeCompare(b[0]));
    let currentGroup: string[] = [];

    sidePorts.forEach(([portName, placement], index) => {
      const previous = sidePorts[index - 1];
      if (previous && placement.offset - previous[1].offset < minimumPortGap) {
        currentGroup = currentGroup.length > 0 ? [...currentGroup, portName] : [previous[0], portName];
      } else if (currentGroup.length > 0) {
        groups.push({ side, ports: currentGroup });
        currentGroup = [];
      }
    });

    if (currentGroup.length > 0) {
      groups.push({ side, ports: currentGroup });
    }
  }

  return groups;
}

export function getPortArrow(direction: PortDirection, side: PortSide): string {
  if (direction === 'both') {
    return side === 'top' || side === 'bottom' ? '↕' : '↔';
  }

  if (direction === 'in') {
    return {
      left: '→',
      right: '←',
      top: '↓',
      bottom: '↑',
    }[side];
  }

  return {
    left: '←',
    right: '→',
    top: '↑',
    bottom: '↓',
  }[side];
}

function chooseSide(
  direction: PortDirection,
  stats: PortStats | undefined,
  loads: Record<PortSide, number>,
  portName: string,
): PortSide {
  const sourceCount = stats?.sourceCount ?? 0;
  const targetCount = stats?.targetCount ?? 0;

  if (direction === 'both' || (sourceCount > 0 && targetCount > 0)) {
    return leastLoaded(['top', 'bottom', 'left', 'right'], loads, portName);
  }

  if (sourceCount > targetCount) {
    return leastLoaded(['right', 'top', 'bottom', 'left'], loads, portName);
  }

  if (targetCount > sourceCount) {
    return leastLoaded(['left', 'top', 'bottom', 'right'], loads, portName);
  }

  if (direction === 'out') {
    return leastLoaded(['right', 'top', 'bottom', 'left'], loads, portName);
  }

  return leastLoaded(['left', 'top', 'bottom', 'right'], loads, portName);
}

function leastLoaded(preferredSides: PortSide[], loads: Record<PortSide, number>, portName: string): PortSide {
  const start = hash(portName) % preferredSides.length;
  const rotated = [...preferredSides.slice(start), ...preferredSides.slice(0, start)];

  return rotated.reduce((best, side) => (loads[side] < loads[best] ? side : best), rotated[0] ?? sideOrder[0]);
}

function hash(value: string): number {
  let result = 0;
  for (let index = 0; index < value.length; index += 1) {
    result = (result * 31 + value.charCodeAt(index)) >>> 0;
  }
  return result;
}

function hasStacking(sidePorts: Array<[string, PortPlacement]>) {
  return sidePorts.some(([, placement], index) => {
    const previous = sidePorts[index - 1];
    return Boolean(previous && placement.offset - previous[1].offset < minimumPortGap);
  });
}

function spreadOffset(index: number, count: number) {
  if (count <= 1) {
    return 0.5;
  }

  return minimumOffset + ((maximumOffset - minimumOffset) * (index + 1)) / (count + 1);
}

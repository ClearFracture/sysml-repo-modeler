import type { PortSide } from './types';

export function sourcePortHandleId(portName: string, side: PortSide): string {
  return `source-${side}-${portName}`;
}

export function targetPortHandleId(portName: string, side: PortSide): string {
  return `target-${side}-${portName}`;
}

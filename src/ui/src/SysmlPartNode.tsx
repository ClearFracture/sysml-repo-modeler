import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useRef, type CSSProperties, type ReactNode } from 'react';
import type {
  DiagramNodeData,
  NodeCompartmentKey,
  PortPlacement,
  PortSide,
  SubPart,
  SysmlMember,
  SysmlPort,
} from './types';
import { sourcePortHandleId, targetPortHandleId } from './portHandles';
import { getEffectiveDirection, getPortArrow } from './portPlacement';
import { formatAttributeDisplay, formatAttributeName } from './sysmlDisplay';

type SysmlPartNodeType = Node<DiagramNodeData, 'sysmlPart'>;

// One renderer for every block. Ports are drawn as anchors on the border (their
// position is a percentage of the box, so they follow the box as it auto-sizes),
// and the box itself is sized by its content via CSS — no manual resizing.
export default function SysmlPartNode({ data, selected }: NodeProps<SysmlPartNodeType>) {
  const {
    instance,
    portPlacements,
    onPortMove,
    onPortSelect,
    compartmentState,
    onCompartmentToggle,
    selectedPortName,
    boundary,
    overview,
    hasSubparts,
  } = data;
  const definition = instance.definition;
  const ports = definition?.ports ?? [];
  const attributes = definition?.attributes ?? [];
  const actions = definition?.actions ?? [];
  const states = definition?.states ?? [];
  const subparts = definition?.parts ?? [];

  return (
    <div
      className={`part-node ${overview ? 'part-node--overview' : ''} ${boundary ? 'part-node--boundary' : ''} ${
        selected ? 'selected' : ''
      }`}
    >
      <div className="part-node__body">
        <div className="part-node__identity">
          <div className="part-node__stereotype">&lt;&lt;part&gt;&gt;</div>
          <div className="part-node__name">{instance.name}</div>
          {overview && hasSubparts && <div className="part-node__drill-hint">double-click to open subparts</div>}
        </div>
        {attributes.length > 0 && (
          <AttributeCompartment
            attributes={attributes}
            onToggle={(open) => onCompartmentToggle?.(instance.name, 'attributes', open)}
            open={compartmentState.attributes}
          />
        )}
        {ports.length > 0 && (
          <PortCompartment
            onToggle={(open) => onCompartmentToggle?.(instance.name, 'ports', open)}
            open={compartmentState.ports}
            ports={ports}
          />
        )}
        {actions.length > 0 && <MemberCompartment title="actions" members={actions} />}
        {states.length > 0 && <MemberCompartment title="states" members={states} />}
        {overview && subparts.length > 0 && <SubpartSummary subparts={subparts} />}
      </div>
      {ports.map((port) => (
        <PortAnchor
          instanceName={instance.name}
          key={port.name}
          onPortMove={onPortMove}
          onPortSelect={onPortSelect}
          placement={portPlacements[port.name] ?? { side: 'right', offset: 0.5 }}
          port={port}
          selected={selectedPortName === port.name}
        />
      ))}
    </div>
  );
}

function MemberCompartment({ members, title }: { members: Array<{ name: string; type?: string }>; title: string }) {
  return (
    <div className="part-node__compartment">
      <div className="part-node__compartment-title">{title}</div>
      <div className="part-node__member-list">
        {members.map((member) => (
          <div className="part-node__relationship-row" key={`${title}-${member.name}`}>
            <span className="part-node__relationship-detail">{formatMember(member)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SubpartSummary({ subparts }: { subparts: SubPart[] }) {
  return (
    <div className="part-node__compartment">
      <div className="part-node__compartment-title">subparts</div>
      <div className="part-node__member-list">
        {subparts.map((subpart) => (
          <div className="part-node__relationship-row" key={`subpart-${subpart.name}`}>
            <span className="part-node__relationship-detail">
              {subpart.name}
              {subpart.ports.length > 0 && (
                <span className="part-node__subpart-ports"> · {subpart.ports.map(formatPortLabel).join(', ')}</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatPortLabel(port: SysmlPort): string {
  return port.type ? `${port.name}: ${port.type}` : port.name;
}

function AttributeCompartment({
  attributes,
  onToggle,
  open,
}: {
  attributes: SysmlMember[];
  onToggle: (open: boolean) => void;
  open: boolean;
}) {
  return (
    <CollapsibleCompartment count={attributes.length} onToggle={onToggle} open={open} title="attributes">
      <div className="part-node__detail-list">
        {attributes.map((attribute) => (
          <div className="part-node__attribute-row" key={`attribute-${attribute.name}`}>
            <strong>{formatAttributeName(attribute.name)}</strong>
            <span>{formatAttributeDisplay(attribute)}</span>
          </div>
        ))}
      </div>
    </CollapsibleCompartment>
  );
}

function formatMember(member: { name: string; type?: string; value?: string }) {
  const type = member.type ? `: ${member.type}` : '';
  const value = member.value ? ` = ${member.value}` : '';
  return `${member.name}${type}${value}`;
}

function PortCompartment({
  onToggle,
  open,
  ports,
}: {
  onToggle: (open: boolean) => void;
  open: boolean;
  ports: SysmlPort[];
}) {
  return (
    <CollapsibleCompartment count={ports.length} onToggle={onToggle} open={open} title="ports">
      <div className="part-node__detail-list">
        {ports.map((port) => {
          const direction = getEffectiveDirection(port.direction);
          return (
            <div className="part-node__port-row" key={port.name}>
              <div className="part-node__port-name-line">
                <strong>{port.name}</strong>
                <span className={`direction-badge ${direction}`}>{direction}</span>
              </div>
              {port.doc ? <span className="part-node__port-doc">{port.doc}</span> : null}
            </div>
          );
        })}
      </div>
    </CollapsibleCompartment>
  );
}

function CollapsibleCompartment({
  children,
  count,
  onToggle,
  open,
  title,
}: {
  children: ReactNode;
  count: number;
  onToggle: (open: boolean) => void;
  open: boolean;
  title: NodeCompartmentKey;
}) {
  return (
    <div className="part-node__compartment">
      <button
        className="part-node__compartment-toggle nodrag nopan"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onToggle(!open);
        }}
        type="button"
      >
        <span className="part-node__compartment-title">{title}</span>
        <span className="part-node__compartment-meta">
          {count}
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {open ? children : null}
    </div>
  );
}

function PortAnchor({
  instanceName,
  onPortMove,
  onPortSelect,
  placement,
  port,
  selected,
}: {
  instanceName: string;
  onPortMove?: (instanceName: string, portName: string, placement: PortPlacement) => void;
  onPortSelect?: (instanceName: string, portName: string) => void;
  placement: PortPlacement;
  port: SysmlPort;
  selected: boolean;
}) {
  const side = placement.side;
  const direction = getEffectiveDirection(port.direction);
  const draggedRef = useRef(false);

  return (
    <div
      className={`port-anchor ${side} ${selected ? 'selected' : ''} nodrag nopan`}
      onPointerDown={(event) => {
        draggedRef.current = false;
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
        event.stopPropagation();
      }}
      onPointerMove={(event) => {
        if (!onPortMove || event.buttons !== 1) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();
        draggedRef.current = true;
        onPortMove(instanceName, port.name, nearestPlacement(event.currentTarget, event.clientX, event.clientY));
      }}
      onPointerUp={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
        if (!draggedRef.current) {
          onPortSelect?.(instanceName, port.name);
        }
        event.stopPropagation();
      }}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!draggedRef.current) {
          onPortSelect?.(instanceName, port.name);
        }
      }}
      style={anchorStyle(placement)}
    >
      <Handle
        id={targetPortHandleId(port.name, side)}
        type="target"
        position={handlePosition[side]}
        className="port-flow-handle"
      />
      <Handle
        id={sourcePortHandleId(port.name, side)}
        type="source"
        position={handlePosition[side]}
        className="port-flow-handle"
      />
      <span className={`port-dot ${direction}`}>{getPortArrow(direction, side)}</span>
      <span className={`port-chip ${direction}`} title={port.doc}>
        <span>{port.name}</span>
      </span>
    </div>
  );
}

const handlePosition: Record<PortSide, Position> = {
  left: Position.Left,
  right: Position.Right,
  top: Position.Top,
  bottom: Position.Bottom,
};

function nearestPlacement(anchorElement: HTMLElement, clientX: number, clientY: number): PortPlacement {
  const nodeElement = anchorElement.closest<HTMLElement>('.part-node') ?? anchorElement;
  const rect = nodeElement.getBoundingClientRect();
  const distances: Array<[PortSide, number]> = [
    ['left', Math.abs(clientX - rect.left)],
    ['right', Math.abs(clientX - rect.right)],
    ['top', Math.abs(clientY - rect.top)],
    ['bottom', Math.abs(clientY - rect.bottom)],
  ];

  distances.sort((a, b) => a[1] - b[1]);
  const side = distances[0][0];
  const offset =
    side === 'left' || side === 'right'
      ? clamp((clientY - rect.top) / rect.height)
      : clamp((clientX - rect.left) / rect.width);

  return { side, offset };
}

// Anchors sit on the border at a percentage of the box's own size, so they track
// the box as its content auto-sizes — no pixel dimensions needed.
function anchorStyle(placement: PortPlacement): CSSProperties {
  const along = `${placement.offset * 100}%`;

  if (placement.side === 'left') {
    return { left: 0, top: along };
  }
  if (placement.side === 'right') {
    return { left: '100%', top: along };
  }
  if (placement.side === 'top') {
    return { left: along, top: 0 };
  }
  return { left: along, top: '100%' };
}

function clamp(value: number) {
  return Math.min(0.98, Math.max(0.02, value));
}

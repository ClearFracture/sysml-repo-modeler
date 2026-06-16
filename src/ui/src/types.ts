export type PortDirection = 'in' | 'out' | 'both';
export type PortSide = 'left' | 'right' | 'top' | 'bottom';

export type PortPlacement = {
  side: PortSide;
  offset: number;
};

export type NodeSize = {
  width: number;
  height: number;
};

export type NodePosition = {
  x: number;
  y: number;
};

export type NodeCompartmentKey = 'attributes' | 'ports';
export type NodeCompartmentState = Record<NodeCompartmentKey, boolean>;

export type NodeContext = {
  id: string;
  label: string;
  column: number;
  row: number;
};

export type SysmlPort = {
  name: string;
  doc?: string;
  direction?: PortDirection;
  type?: string;
  attributes: SysmlMember[];
};

export type SysmlMember = {
  name: string;
  type?: string;
  value?: string;
};

export type SubPart = {
  name: string;
  doc?: string;
  ports: SysmlPort[];
  attributes: SysmlMember[];
  parts: SubPart[];
};

export type PartDefinition = {
  name: string;
  doc?: string;
  ports: SysmlPort[];
  attributes: SysmlMember[];
  actions: SysmlMember[];
  states: SysmlMember[];
  parts: SubPart[];
};

export type PartInstance = {
  name: string;
  type: string;
  definition?: PartDefinition;
};

// A connection endpoint addressed as instance(.subpart)*(.port)? with an optional
// port-type annotation (the `connect a to b : HttpPort;` form). `path` holds the
// subpart chain between the instance and the port.
export type Endpoint = {
  instance: string;
  path: string[];
  port?: string;
  portType?: string;
};

// The raw, fully-addressed connection produced by the parser. Projections lower
// this into the flat `SysmlConnection` shape the diagram pipeline consumes.
export type ParsedConnection = {
  id: string;
  source: Endpoint;
  target: Endpoint;
};

export type SysmlConnection = {
  id: string;
  sourcePart: string;
  sourcePort: string;
  targetPart: string;
  targetPort: string;
};

export type SysmlView = {
  name: string;
  type?: string;
  expose?: string;
};

export type SysmlSystem = {
  name: string;
  doc?: string;
  instances: PartInstance[];
  connections: ParsedConnection[];
};

export type SysmlModel = {
  packageName?: string;
  partDefinitions: Map<string, PartDefinition>;
  systems: SysmlSystem[];
  selectedSystem: SysmlSystem;
  view?: SysmlView;
  diagnostics: string[];
};

export type EdgeFilter = 'all' | 'selected' | 'ingress' | 'data' | 'external';

export type DiagramNodeData = Record<string, unknown> & {
  instance: PartInstance;
  context: NodeContext;
  portRelations: Record<string, string[]>;
  portPlacements: Record<string, PortPlacement>;
  lockedPortPlacements: Record<string, boolean>;
  onPortMove?: (instanceName: string, portName: string, placement: PortPlacement) => void;
  onPortSelect?: (instanceName: string, portName: string) => void;
  compartmentState: NodeCompartmentState;
  onCompartmentToggle?: (instanceName: string, compartment: NodeCompartmentKey, open: boolean) => void;
  selectedPortName?: string;
  connectedCount: number;
  highlighted: boolean;
  dimmed: boolean;
  // Top-level overview node: renders a read-only subpart/port summary instead of
  // draggable port anchors. Set only by buildTopLevelElements.
  overview?: boolean;
  // Collapsed external reference shown at the edge of an excerpt view.
  boundary?: boolean;
  // Whether the (overview) instance owns subparts worth drilling into.
  hasSubparts?: boolean;
};

// Summary of all raw connections collapsed into a single overview edge between
// one instance pair.
export type EdgeAggregate = {
  sourceInstance: string;
  targetInstance: string;
  count: number;
  portTypes: string[];
  pairs: string[];
};

export type DiagramEdgeData = Record<string, unknown> & {
  connection?: SysmlConnection;
  aggregate?: EdgeAggregate;
  highlighted: boolean;
  dimmed: boolean;
};

export type Selection =
  | { kind: 'node'; id: string }
  | { kind: 'edge'; id: string }
  | { kind: 'port'; nodeId: string; portName: string }
  | { kind: 'none' };

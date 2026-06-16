import { Activity, ArrowRight, Box, ChevronDown, CircleDot, FileText, FileWarning, PlugZap } from 'lucide-react';
import type { Edge, Node } from '@xyflow/react';
import type { DiagramEdgeData, DiagramNodeData, Endpoint, SysmlMember, SysmlModel } from './types';
import {
  findAttributeValue,
  formatAttributeDisplay,
  formatAttributeName,
  formatEnumValue,
  formatProtocol,
} from './sysmlDisplay';

type DetailsPanelProps = {
  model: SysmlModel;
  selectedNode?: Node<DiagramNodeData>;
  selectedEdge?: Edge<DiagramEdgeData>;
  selectedPort?: { node: Node<DiagramNodeData>; portName: string };
};

export default function DetailsPanel({ model, selectedNode, selectedEdge, selectedPort }: DetailsPanelProps) {
  if (selectedPort) {
    const instance = selectedPort.node.data.instance;
    const port = instance.definition?.ports.find((candidate) => candidate.name === selectedPort.portName);
    const connections = model.selectedSystem.connections.filter(
      (connection) =>
        endpointMatchesPart(connection.source, instance.name, selectedPort.portName) ||
        endpointMatchesPart(connection.target, instance.name, selectedPort.portName),
    );
    const interfaceFacts = port ? getPortInterfaceFacts(port.attributes) : [];

    return (
      <aside className="details-panel">
        <PanelTitle
          icon={<PlugZap size={18} />}
          title={port?.name ?? selectedPort.portName}
          subtitle={`${instance.name}.${selectedPort.portName} - ${port?.direction ?? 'both'}`}
        />
        {port?.doc ? (
          <CollapsibleSection icon={<FileText size={15} />} title="Documentation">
            <p className="details-copy">{port.doc}</p>
          </CollapsibleSection>
        ) : null}
        {interfaceFacts.length > 0 ? (
          <CollapsibleSection icon={<PlugZap size={15} />} title="Interface">
            <div className="attribute-list">
              {interfaceFacts.map((fact) => (
                <div className="attribute-row" key={fact.label}>
                  <strong>{fact.label}</strong>
                  <span>{fact.value}</span>
                </div>
              ))}
            </div>
          </CollapsibleSection>
        ) : null}
        {port?.attributes.length ? (
          <CollapsibleSection
            count={port.attributes.length}
            defaultOpen={false}
            icon={<FileText size={15} />}
            title="Attributes"
          >
            <div className="attribute-list">
              {port.attributes.map((attribute) => (
                <div className="attribute-row" key={attribute.name}>
                  <strong>{formatAttributeName(attribute.name)}</strong>
                  <span>{formatAttributeDisplay(attribute)}</span>
                </div>
              ))}
            </div>
          </CollapsibleSection>
        ) : null}
        <CollapsibleSection count={connections.length} icon={<Activity size={15} />} title="Connected Endpoints">
          <div className="connection-list">
            {connections.map((connection) => (
              <div className="connection-row" key={connection.id}>
                <span>{endpointAddress(connection.source)}</span>
                <ArrowRight size={14} />
                <span>{endpointAddress(connection.target)}</span>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      </aside>
    );
  }

  if (selectedNode) {
    const instance = selectedNode.data.instance;
    const definition = instance.definition;
    const connections = model.selectedSystem.connections.filter(
      (connection) => connection.source.instance === instance.name || connection.target.instance === instance.name,
    );

    return (
      <aside className="details-panel">
        <PanelTitle icon={<Box size={18} />} title={instance.name} subtitle={instance.type} />
        {definition?.doc ? (
          <CollapsibleSection icon={<FileText size={15} />} title="Documentation">
            <p className="details-copy">{definition.doc}</p>
          </CollapsibleSection>
        ) : null}
        <CollapsibleSection count={connections.length} icon={<Activity size={15} />} title="Connected Endpoints">
          <div className="connection-list">
            {connections.map((connection) => (
              <div className="connection-row" key={connection.id}>
                <span>{endpointAddress(connection.source)}</span>
                <ArrowRight size={14} />
                <span>{endpointAddress(connection.target)}</span>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      </aside>
    );
  }

  if (selectedEdge) {
    const aggregate = selectedEdge.data?.aggregate;
    if (aggregate) {
      return (
        <aside className="details-panel">
          <PanelTitle
            icon={<CircleDot size={18} />}
            title="Connections"
            subtitle={`${aggregate.sourceInstance} to ${aggregate.targetInstance}`}
          />
          <CollapsibleSection icon={<Activity size={15} />} title="Summary">
            <div className="attribute-list">
              <div className="attribute-row">
                <strong>Count</strong>
                <span>{aggregate.count}</span>
              </div>
              {aggregate.portTypes.length > 0 ? (
                <div className="attribute-row">
                  <strong>Port types</strong>
                  <span>{aggregate.portTypes.join(', ')}</span>
                </div>
              ) : null}
            </div>
          </CollapsibleSection>
          <CollapsibleSection count={aggregate.pairs.length} icon={<Activity size={15} />} title="Connections">
            <div className="connection-list">
              {aggregate.pairs.map((pair) => (
                <div className="connection-row" key={pair}>
                  <span>{pair}</span>
                </div>
              ))}
            </div>
          </CollapsibleSection>
        </aside>
      );
    }

    const connection = selectedEdge.data?.connection;
    if (!connection) {
      return null;
    }

    const source = model.selectedSystem.instances.find((instance) => instance.name === connection.sourcePart);
    const target = model.selectedSystem.instances.find((instance) => instance.name === connection.targetPart);

    return (
      <aside className="details-panel">
        <PanelTitle
          icon={<CircleDot size={18} />}
          title="Connection"
          subtitle={`${connection.sourcePart} to ${connection.targetPart}`}
        />
        <CollapsibleSection icon={<Activity size={15} />} title="Endpoints">
          <div className="endpoint-block">
            <span>Source</span>
            <strong>
              {connection.sourcePart}.{connection.sourcePort}
            </strong>
            <small>{source?.type}</small>
          </div>
          <div className="endpoint-block">
            <span>Target</span>
            <strong>
              {connection.targetPart}.{connection.targetPort}
            </strong>
            <small>{target?.type}</small>
          </div>
        </CollapsibleSection>
      </aside>
    );
  }

  return (
    <aside className="details-panel">
      <PanelTitle
        icon={<CircleDot size={18} />}
        title={model.selectedSystem.name}
        subtitle={model.view?.name ?? model.packageName}
      />
      {model.selectedSystem.doc ? (
        <CollapsibleSection icon={<FileText size={15} />} title="Documentation">
          <p className="details-copy">{model.selectedSystem.doc}</p>
        </CollapsibleSection>
      ) : null}
      <CollapsibleSection
        count={model.selectedSystem.instances.length}
        defaultOpen={false}
        icon={<Box size={15} />}
        title="Parts"
      >
        <div className="port-list">
          {model.selectedSystem.instances.map((instance) => (
            <div className="port-row" key={instance.name}>
              <span className="direction-dot neutral" />
              <div>
                <div className="port-row__name">{instance.name}</div>
                <div className="port-row__meta">{instance.type}</div>
              </div>
            </div>
          ))}
        </div>
      </CollapsibleSection>
      <CollapsibleSection
        count={model.selectedSystem.connections.length}
        defaultOpen={false}
        icon={<Activity size={15} />}
        title="Connections"
      >
        <div className="connection-list">
          {model.selectedSystem.connections.map((connection) => (
            <div className="connection-row" key={connection.id}>
              <span>{endpointAddress(connection.source)}</span>
              <ArrowRight size={14} />
              <span>{endpointAddress(connection.target)}</span>
            </div>
          ))}
        </div>
      </CollapsibleSection>
      {model.diagnostics.length > 0 ? (
        <CollapsibleSection count={model.diagnostics.length} icon={<FileWarning size={15} />} title="Diagnostics">
          <div className="diagnostics-list">
            {model.diagnostics.map((diagnostic) => (
              <div key={diagnostic}>{diagnostic}</div>
            ))}
          </div>
        </CollapsibleSection>
      ) : null}
    </aside>
  );
}

function endpointAddress(endpoint: Endpoint): string {
  return [endpoint.instance, ...endpoint.path, endpoint.port].filter(Boolean).join('.');
}

function endpointMatchesPart(endpoint: Endpoint, partName: string, portName: string): boolean {
  return endpoint.port === portName && (endpoint.instance === partName || endpoint.path.includes(partName));
}

function getPortInterfaceFacts(attributes: SysmlMember[]) {
  const facts: Array<{ label: string; value: string }> = [];
  const protocol = formatProtocol(attributes);
  const networkPort = findAttributeValue(attributes, 'networkPort');
  const authentication = findAttributeValue(attributes, 'authScheme');
  const timeout = findAttributeValue(attributes, 'timeoutSeconds');
  const maxConnections = findAttributeValue(attributes, 'maxConnections');
  const exposure = findAttributeValue(attributes, 'exposure');

  if (protocol) {
    facts.push({ label: 'Protocol', value: protocol });
  }
  if (networkPort) {
    facts.push({ label: 'Port', value: networkPort });
  }
  if (authentication) {
    facts.push({ label: 'Authentication', value: formatEnumValue(authentication) });
  }
  if (timeout) {
    facts.push({ label: 'Timeout', value: `${timeout}s` });
  }
  if (maxConnections) {
    facts.push({ label: 'Max Connections', value: maxConnections });
  }
  if (exposure) {
    facts.push({ label: 'Exposure', value: formatEnumValue(exposure) });
  }

  return facts;
}

function PanelTitle({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle?: string }) {
  return (
    <div className="panel-title">
      {icon}
      <div>
        <h2>{title}</h2>
        {subtitle ? <span>{subtitle}</span> : null}
      </div>
    </div>
  );
}

function CollapsibleSection({
  children,
  count,
  defaultOpen = true,
  icon,
  title,
}: {
  children: React.ReactNode;
  count?: number;
  defaultOpen?: boolean;
  icon: React.ReactNode;
  title: string;
}) {
  return (
    <details className="details-section" open={defaultOpen}>
      <summary className="details-section__summary">
        <span className="details-section__label">
          {icon}
          <span>{title}</span>
        </span>
        <span className="details-section__meta">
          {typeof count === 'number' ? <span>{count}</span> : null}
          <ChevronDown size={15} />
        </span>
      </summary>
      <div className="details-section__body">{children}</div>
    </details>
  );
}

SELECT
    scenario,
    (
        visual_workflow + multi_agent + workflow_breadth + hitl + durability +
        local_selfhost + distributed_fleet + resource_routing + rag_memory +
        integrations_mcp + observability + evaluation + security_governance +
        enterprise_scale + end_user_channels
    ) / 60.0 AS breadth,
    (
        visual_workflow * 6 + multi_agent * 6 + workflow_breadth * 7 + hitl * 6 +
        durability * 7 + local_selfhost * 9 + distributed_fleet * 14 +
        resource_routing * 9 + rag_memory * 7 + integrations_mcp * 7 +
        observability * 6 + evaluation * 5 + security_governance * 6 +
        enterprise_scale * 3 + end_user_channels * 2
    ) / 400.0 AS sovereign_fit,
    (
        visual_workflow * 6 + multi_agent * 6 + workflow_breadth * 9 + hitl * 8 +
        durability * 9 + local_selfhost * 2 + distributed_fleet * 1 +
        resource_routing * 4 + rag_memory * 8 + integrations_mcp * 12 +
        observability * 8 + evaluation * 7 + security_governance * 9 +
        enterprise_scale * 7 + end_user_channels * 4
    ) / 400.0 AS enterprise_fit,
    description AS meaning
FROM competitive_analysis_scenarios
ORDER BY rowid;

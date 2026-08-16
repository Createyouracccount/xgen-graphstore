"""FusekiBackend — OntologyStore 의 Fuseki 구현 (2층 이관).

FusekiClient 를 상속해 기존 transport(sparql_query/sparql_update/upload_ttl/…)를 그대로
쓰면서, 인라인 SPARQL 을 걷어낸 **의미 메서드**를 추가한다.
호출부는 이미 `self.fuseki`/`pipeline.fuseki` 로 클라이언트 인스턴스를 들고 있으므로,
생성 지점(main.py, pipeline.py)에서 FusekiClient → FusekiBackend 로만 바꾸면
기존 호출은 무변경으로 계속 동작하고 새 의미 메서드가 함께 노출된다.

⚠️ 동작 보존: 각 메서드는
  - 쿼리를 fuseki_queries 의 순수 빌더로 만들고(바이트 동일),
  - 파싱은 이관 전 호출부 로직을 그대로 옮긴다(파싱 등가).
쿼리·파싱 개선 금지.
"""

from __future__ import annotations

from typing import Any, Dict, List

from xgen_graphstore.transport import FusekiClient
from xgen_graphstore.capabilities import Capability
from xgen_graphstore import queries as q


class FusekiBackend(FusekiClient):
    """OntologyStore 의 Apache Jena Fuseki 구현."""

    BACKEND_NAME = "fuseki"
    # RDF/SPARQL 원본 — 모든 능력 보유(2층 이관의 기준 백엔드).
    CAPABILITIES = frozenset(Capability)

    # ── B1: graph_rag 순수 READ ──

    async def node_properties(self, graph_name: str, node_uri: str) -> List[Dict[str, Any]]:
        """원본: get_node_properties (graph_rag_operations.py ~254-271)."""
        result = await self.sparql_query(q.node_properties_query(graph_name, node_uri))
        properties: List[Dict[str, Any]] = []
        for b in result.get("results", {}).get("bindings", []):
            p_uri = b["p"]["value"]
            p_label = b.get("pLabel", {}).get("value", p_uri.split("#")[-1] if "#" in p_uri else p_uri.split("/")[-1])
            o_val = b["o"]["value"]
            o_label = b.get("oLabel", {}).get("value", "")
            o_type = b["o"].get("type", "literal")
            if any(skip in p_uri for skip in ["rdf-syntax-ns#type", "owl#", "w3.org/2000/01/rdf-schema#"]):
                continue
            properties.append({
                "property": p_label,
                "property_uri": p_uri,
                "value": o_label or o_val,
                "value_type": o_type,
            })
        return properties

    async def property_values(
        self, graph_name: str, property_uri: str, limit: int
    ) -> List[Dict[str, Any]]:
        """원본: get_property_values (graph_rag_operations.py ~304-315)."""
        result = await self.sparql_query(q.property_values_query(graph_name, property_uri, limit))
        values: List[Dict[str, Any]] = []
        for b in result.get("results", {}).get("bindings", []):
            inst_uri = b["instance"]["value"]
            inst_label = b.get("instanceLabel", {}).get("value", inst_uri.split("#")[-1] if "#" in inst_uri else inst_uri.split("/")[-1])
            val = b["value"]["value"]
            values.append({
                "instance": inst_label,
                "instance_uri": inst_uri,
                "value": val,
            })
        return values

    async def neighbors(self, graph_name: str, node_uri: str) -> Dict[str, List[Dict[str, Any]]]:
        """원본: explore_node (graph_rag_operations.py ~458-503).

        outgoing/incoming 두 쿼리를 순차 전송하고 파싱한다(원본과 동일 순서).
        """
        out_result = await self.sparql_query(q.neighbors_out_query(graph_name, node_uri))
        in_result = await self.sparql_query(q.neighbors_in_query(graph_name, node_uri))

        outgoing: List[Dict[str, Any]] = []
        for b in out_result.get("results", {}).get("bindings", []):
            p_uri = b["p"]["value"]
            p_label = b.get("pLabel", {}).get("value", p_uri.split("#")[-1] if "#" in p_uri else p_uri.split("/")[-1])
            target_uri = b["target"]["value"]
            target_label = b.get("targetLabel", {}).get("value", target_uri.split("#")[-1] if "#" in target_uri else target_uri.split("/")[-1])
            target_type = b.get("targetType", {}).get("value", "")
            if any(skip in p_uri for skip in ["sourceChunk", "sourceDocument", "owl#", "rdf-syntax"]):
                continue
            outgoing.append({
                "relation": p_label,
                "relation_uri": p_uri,
                "target": target_label,
                "target_uri": target_uri,
                "target_class": target_type,
                "direction": "out",
            })

        incoming: List[Dict[str, Any]] = []
        for b in in_result.get("results", {}).get("bindings", []):
            source_uri = b["source"]["value"]
            source_label = b.get("sourceLabel", {}).get("value", source_uri.split("#")[-1] if "#" in source_uri else source_uri.split("/")[-1])
            source_type = b.get("sourceType", {}).get("value", "")
            p_uri = b["p"]["value"]
            p_label = b.get("pLabel", {}).get("value", p_uri.split("#")[-1] if "#" in p_uri else p_uri.split("/")[-1])
            if any(skip in p_uri for skip in ["sourceChunk", "sourceDocument", "owl#", "rdf-syntax"]):
                continue
            incoming.append({
                "relation": p_label,
                "relation_uri": p_uri,
                "source": source_label,
                "source_uri": source_uri,
                "source_class": source_type,
                "direction": "in",
            })

        return {"outgoing": outgoing, "incoming": incoming}

    async def triple_exists(self, graph_name: str, s: str, p: str, o: str) -> bool:
        """원본: _triple_exists (graph_rag_operations.py ~533-534)."""
        res = await self.sparql_query(q.triple_exists_query(graph_name, s, p, o))
        return bool(res.get("boolean", False))

    async def count_node_triples(self, graph_name: str, node_uri: str) -> int:
        """원본: _count_node_triples (graph_rag_operations.py ~777-781)."""
        res = await self.sparql_query(q.count_node_triples_query(graph_name, node_uri))
        try:
            return int(res["results"]["bindings"][0]["c"]["value"])
        except Exception:
            return 0

    # ── B2: graph_rag READ 집계 ──

    async def class_instance_counts(self, graph_name: str) -> List[Dict[str, Any]]:
        """원본: diagnose_kg abox_stats (graph_rag_operations.py ~1300-1306)."""
        abox_result = await self.sparql_query(q.class_instance_counts_query(graph_name))
        abox_stats: List[Dict[str, Any]] = []
        for b in abox_result.get("results", {}).get("bindings", []):
            abox_stats.append({
                "class": b.get("classLabel", {}).get("value", ""),
                "instance_count": int(b.get("instanceCount", {}).get("value", "0")),
            })
        return abox_stats

    async def relation_triple_counts(self, graph_name: str) -> List[Dict[str, Any]]:
        """원본: diagnose_kg relation_stats (graph_rag_operations.py ~1326-1333)."""
        rel_result = await self.sparql_query(q.relation_triple_counts_query(graph_name))
        relation_stats: List[Dict[str, Any]] = []
        for b in rel_result.get("results", {}).get("bindings", []):
            prop_label = b.get("propLabel", {}).get("value", "(unlabeled)")
            relation_stats.append({
                "property": prop_label,
                "triple_count": int(b.get("count", {}).get("value", "0")),
            })
        return relation_stats

    # ── B3: community_detect ──
    # 파싱은 호출부(도메인 로직: node_set/edges/label_of 조립)에 남기고, 여기선 쿼리 방출만.

    async def community_edges(self, graph_name: str):
        """원본: detect_and_tag_communities q_edges (community_detect.py ~104). 원 결과 반환."""
        return await self.sparql_query(q.community_edges_query(graph_name))

    async def community_labels(self, graph_name: str):
        """원본: detect_and_tag_communities q_lab (community_detect.py ~126). 원 결과 반환."""
        return await self.sparql_query(q.community_labels_query(graph_name))

    async def tag_communities(self, graph_name: str, comm_of: Dict[str, Any]) -> None:
        """원본: detect_and_tag_communities 태그 적재 (community_detect.py ~139-146).

        기존 태그 DELETE → 배치 INSERT 순서·triples 조립 바이트 보존.
        호출부의 try/except 는 호출부에 남긴다(예외 경계 무변경).
        """
        await self.sparql_update(q.community_tag_delete_update(graph_name))
        triples = " ".join(
            f"<{uri}> <{q._NS}community> {comm} ." for uri, comm in comm_of.items()
        )
        if triples:
            await self.sparql_update(q.community_tag_insert_update(graph_name, triples))

    # ── B3: multi_turn_rag seed (일부 text:query=jena-text 전용 — 원장 부채) ──
    # 시드 파싱은 호출부에 남기고(관계/클래스 조립), 여기선 쿼리 방출·원 결과 반환.

    async def seed_chunk_relations(self, graph_name: str, values: str, limit: int):
        """원본: multi_turn_rag gq (~223). VALUES 청크 1홉 시드."""
        return await self.sparql_query(q.seed_chunk_relations_query(graph_name, values, limit))

    async def predicate_labels(self, graph_name: str):
        """원본: multi_turn_rag _pred_labels (~503). 순수 SPARQL."""
        return await self.sparql_query(q.predicate_labels_query(graph_name))

    async def seed_relations_by_fulltext_forward(self, graph_name: str, terms: str, pin: str):
        """원본: multi_turn_rag qf (~561). ⚠️text:query — 부채."""
        return await self.sparql_query(
            q.seed_relations_by_fulltext_forward_query(graph_name, terms, pin))

    async def seed_relations_by_fulltext_reverse(self, graph_name: str, terms: str, pin: str):
        """원본: multi_turn_rag qr (~561). ⚠️text:query — 부채."""
        return await self.sparql_query(
            q.seed_relations_by_fulltext_reverse_query(graph_name, terms, pin))

    async def seed_connectivity_relations(self, graph_name: str, terms: str, limit: int):
        """원본: multi_turn_rag q_conn (~606). ⚠️text:query — 부채."""
        return await self.sparql_query(
            q.seed_connectivity_relations_query(graph_name, terms, limit))

    async def seed_relations_broad(self, graph_name: str, terms: str, limit: int):
        """원본: multi_turn_rag q_broad (~621). ⚠️text:query — 부채."""
        return await self.sparql_query(
            q.seed_relations_broad_query(graph_name, terms, limit))

    async def seed_classes_by_fulltext(self, graph_name: str, terms: str):
        """원본: multi_turn_rag q (~647). ⚠️text:query — 부채."""
        return await self.sparql_query(q.seed_classes_by_fulltext_query(graph_name, terms))

    # ── B4: graph_rag WRITE ──
    # ASK 멱등가드(triple_exists 사전확인·사후검증)는 호출부에 유지. 여기선 방출·전송만.

    async def insert_data(self, graph_name: str, triple_lines: str) -> bool:
        """원본: _insert_triples INSERT (graph_rag_operations.py ~443)."""
        return await self.sparql_update(q.insert_data_update(graph_name, triple_lines))

    async def delete_data(self, graph_name: str, triple_lines: str) -> bool:
        """원본: _delete_triples DELETE (graph_rag_operations.py ~549)."""
        return await self.sparql_update(q.delete_data_update(graph_name, triple_lines))

    async def delete_node_subject_side(self, graph_name: str, node_uri: str) -> bool:
        """원본: delete_node subject-측 (graph_rag_operations.py ~656)."""
        return await self.sparql_update(q.delete_node_subject_update(graph_name, node_uri))

    async def delete_node_object_side(self, graph_name: str, node_uri: str) -> bool:
        """원본: delete_node object-측 (graph_rag_operations.py ~659)."""
        return await self.sparql_update(q.delete_node_object_update(graph_name, node_uri))

    # ── B5: pipeline 병합/rename (HARD — 2면 triple 이동) ──
    # canonical/uri/label 선정은 호출부(도메인)에 유지. 여기선 방출·전송만.

    async def merge_move_subject(self, graph_name: str, uri: str, canonical: str) -> bool:
        """원본: _merge_* 주어면 이동 (pipeline.py ~2772/2827)."""
        return await self.sparql_update(q.merge_move_subject_update(graph_name, uri, canonical))

    async def merge_move_object(self, graph_name: str, uri: str, canonical: str) -> bool:
        """원본: _merge_* 목적어면 이동 (pipeline.py ~2777/2832)."""
        return await self.sparql_update(q.merge_move_object_update(graph_name, uri, canonical))

    async def merge_normalized_instances_labels(self, graph_name: str):
        """원본: _merge_normalized_instances SELECT (pipeline.py ~2736). 원 결과 반환."""
        return await self.sparql_query(q.merge_normalized_instances_select(graph_name))

    async def same_label_nodes(self, graph_name: str, rdfs: str, type_uri: str):
        """원본: _merge_same_label_nodes SELECT (pipeline.py ~2806). 원 결과 반환."""
        return await self.sparql_query(q.merge_same_label_select(graph_name, rdfs, type_uri))

    async def rename_move_subject(self, graph_name: str, rdfs: str, ol: str, nl: str) -> bool:
        """원본: _apply_rename_to_graph 주어면 (pipeline.py ~2894)."""
        return await self.sparql_update(q.rename_move_subject_update(graph_name, rdfs, ol, nl))

    async def rename_move_object(self, graph_name: str, rdfs: str, ol: str, nl: str) -> bool:
        """원본: _apply_rename_to_graph 목적어면 (pipeline.py ~2901)."""
        return await self.sparql_update(q.rename_move_object_update(graph_name, rdfs, ol, nl))

    async def rename_drop_old_label(self, graph_name: str, rdfs: str, ol: str, nl: str) -> bool:
        """원본: _apply_rename_to_graph old 라벨 제거 (pipeline.py ~2908)."""
        return await self.sparql_update(q.rename_drop_old_label_update(graph_name, rdfs, ol, nl))

"""
LangChain tool for querying the Comex Agent Graph Memory.
"""
import json
import logging
from langchain_core.tools import tool as langchain_tool

logger = logging.getLogger(__name__)


def make_search_memory_tool(graph_store, agent_id: str, step_ref: dict, seen_excerpts: set = None):
    if seen_excerpts is None:
        seen_excerpts = set()

    @langchain_tool
    def search_memory(query: str, domain: str = "ALL") -> str:
        """Check your persistent, long-term memory. 
        You can search across two domains:
        - "ENTITIES": For structured data you extracted previously (e.g., products, prices).
        - "HISTORY": For your past tasks, actions, and the raw text results of your previous tool calls.
        - "ALL": To search everything.
        Always use search_memory before browsing if you suspect you or the user have already encountered the requested information.
        """
        try:
            results = graph_store.search_memory(query, domain)
            
            # Deduplicate memory already seen in context if the excerpt is exactly the same
            filtered = []
            for r in results:
                excerpt_hash = hash(r.get("content_excerpt", ""))
                if excerpt_hash not in seen_excerpts:
                    filtered.append(r)
                    seen_excerpts.add(excerpt_hash)
            
            try:
                graph_store.record_tool_call(
                    agent_id, "search_memory",
                    args_json=json.dumps({"query": query, "domain": domain}),
                    result_summary=f"{len(filtered)} memory items retrieved",
                    step_index=step_ref.get("value", 0),
                    success=True
                )
            except Exception as re:
                logger.warning(f"Graph: failed to record search_memory tool call: {re}")

            if filtered:
                return json.dumps(filtered, indent=2, default=str)
            return "No matching memory found."
        except Exception as e:
            logger.warning(f"Graph memory search failed: {e}")
            return f"Graph memory search failed: {e}"

    return search_memory

def make_save_artifact_tool(graph_store, agent_id: str, step_ref: dict):
    @langchain_tool
    def save_artifact(title: str, artifact_type: str, content: str) -> str:
        """Save a completed result or durable unstructured knowledge as an Artifact.
        Use this tool to persist final reports, collected lists, research summaries,
        or exact sets of questions.
        """
        try:
            effective_title = (title or "Untitled Artifact").strip()
            effective_type = (artifact_type or "notes").strip()
            artifact_id = graph_store.save_artifact(agent_id, effective_title, effective_type, content)
            if artifact_id:
                try:
                    graph_store.record_tool_call(
                        agent_id, "save_artifact",
                        args_json=json.dumps({"title": effective_title, "artifact_type": effective_type, "content_length": len(content)}),
                        result_summary=f"Saved artifact {artifact_id} ({len(content)} chars)",
                        step_index=step_ref.get("value", 0),
                        success=True
                    )
                except Exception as re:
                    logger.warning(f"Graph: failed to record save_artifact tool call: {re}")
                return f"Successfully saved artifact '{effective_title}' (ID: {artifact_id})."
            return "Failed to save artifact."
        except Exception as e:
            logger.warning(f"save_artifact failed: {e}")
            return f"save_artifact failed: {e}"
    
    return save_artifact

def make_read_artifact_tool(graph_store, agent_id: str, step_ref: dict, chunk_size: int = 4000):
    @langchain_tool
    def read_artifact(artifact_id: str, chunk_index: int = 0) -> str:
        """Read the full content of an Artifact sequentially by chunk.
        Use this tool after finding an artifact_id (source_id) via search_memory
        to read its complete text without being bounded to a short excerpt.
        """
        try:
            if chunk_index < 0:
                return json.dumps({"error": "validation_error", "message": "chunk_index cannot be negative"})
                
            if not artifact_id.startswith("art_"):
                return json.dumps({"error": "invalid_id", "message": f"Malformed Artifact ID: {artifact_id}"})
                
            artifact = graph_store.get_artifact(artifact_id)
            if not artifact:
                return json.dumps({"error": "not_found", "message": f"Artifact {artifact_id} not found."})
                
            content = artifact.get("content", "")
            content_len = len(content)
            
            if content_len == 0:
                total_chunks = 1
                start = 0
                end = 0
                chunk_content = ""
                has_more = False
            else:
                import math
                total_chunks = math.ceil(content_len / chunk_size)
                if chunk_index >= total_chunks:
                    return json.dumps({"error": "out_of_range", "message": f"chunk_index {chunk_index} is out of range. total_chunks is {total_chunks}"})
                
                start = chunk_index * chunk_size
                end = min(start + chunk_size, content_len)
                chunk_content = content[start:end]
                has_more = end < content_len
                
            wrapped_content = f"[UNTRUSTED DATA START]\n{chunk_content}\n[UNTRUSTED DATA END]"
            
            result = {
                "artifact_id": artifact.get("id"),
                "title": artifact.get("title"),
                "artifact_type": artifact.get("artifact_type"),
                "created_at": artifact.get("created_at"),
                "source_agent_id": artifact.get("source_agent_id"),
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "char_range": f"{start}-{end}",
                "content": wrapped_content,
                "has_more": has_more
            }
            
            try:
                graph_store.record_tool_call(
                    agent_id, "read_artifact",
                    args_json=json.dumps({"artifact_id": artifact_id, "chunk_index": chunk_index}),
                    result_summary=f"Read artifact chunk {chunk_index}/{total_chunks} ({len(chunk_content)} chars)",
                    step_index=step_ref.get("value", 0),
                    success=True
                )
            except Exception as re:
                logger.warning(f"Graph: failed to record read_artifact tool call: {re}")
                
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.warning(f"read_artifact failed: {e}")
            return json.dumps({"error": "graph_failure", "message": str(e)})

    return read_artifact


def make_save_user_memory_tool(graph_store, agent_id: str, step_ref: dict):
    @langchain_tool
    def save_user_memory(key: str, value: str, category: str = "preference") -> str:
        """Save durable knowledge about the user, such as preferences, profile facts, or constraints.
        Categories: 'preference' (e.g. currency, preferred brands), 'profile' (e.g. location, tech stack),
        'constraint' (e.g. budget limit, non-negotiable requirements).
        """
        try:
            mem_id = graph_store.set_user_memory(key=key, value=value, category=category, agent_id=agent_id)
            try:
                graph_store.record_tool_call(
                    agent_id, "save_user_memory",
                    args_json=json.dumps({"key": key, "value": value, "category": category}),
                    result_summary=f"Saved user memory '{key}' (ID: {mem_id})",
                    step_index=step_ref.get("value", 0),
                    success=True
                )
            except Exception as re:
                logger.warning(f"Graph: failed to record save_user_memory tool call: {re}")
            return f"Successfully saved user memory: {key} = {value} (Category: {category})"
        except Exception as e:
            logger.warning(f"save_user_memory failed: {e}")
            return f"Failed to save user memory: {e}"

    return save_user_memory


def make_save_skill_tool(graph_store, agent_id: str, step_ref: dict):
    @langchain_tool
    def save_skill(name: str, domain: str, description: str, code_snippet: str) -> str:
        """Save a distilled browser interaction skill or recipe that can be reused for future tasks.
        Domain should be the target site (e.g. 'amazon.in', 'youtube.com') or '*' for generic skills.
        Code snippet must be executable Python code suitable for run_browser_harness.
        """
        try:
            skill_id = graph_store.save_skill(name=name, domain=domain, description=description, code_snippet=code_snippet, agent_id=agent_id)
            try:
                graph_store.record_tool_call(
                    agent_id, "save_skill",
                    args_json=json.dumps({"name": name, "domain": domain, "description": description}),
                    result_summary=f"Saved skill '{name}' for domain {domain} (ID: {skill_id})",
                    step_index=step_ref.get("value", 0),
                    success=True
                )
            except Exception as re:
                logger.warning(f"Graph: failed to record save_skill tool call: {re}")
            return f"Successfully saved skill '{name}' for {domain} (ID: {skill_id})."
        except Exception as e:
            logger.warning(f"save_skill failed: {e}")
            return f"Failed to save skill: {e}"

    return save_skill

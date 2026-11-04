import unittest
from unittest.mock import Mock

from agat_worker import CoordinatorClient, LocalModelClient


class ExtractionCoverageTests(unittest.TestCase):
    def test_extraction_limits_reach_the_model_context_without_operator_reason(self):
        coordinator = Mock(spec=CoordinatorClient)
        coordinator.knowledge_search.return_value = {"hits": [{"marker": "K1", "content": "Known facts only", "provenance": {
            "documentName": "Source.pdf", "extraction": {"emptyPageCount": 2, "omittedPartsCount": None, "fidelity": "not_assessed"}}}]}
        client = LocalModelClient("http://unused.invalid/v1", "", coordinator_client=coordinator)
        result = client.retrieve_knowledge({"leaseId": "fixture", "run": {"input": "Question"}, "stage": {}, "knowledge": {
            "groups": [{"embeddingModel": "fixture", "collectionIds": ["collection"], "topK": 1}], "memory": []}}, dry_run=True)
        self.assertIn("[K1]", result)
        self.assertIn("Known facts only", result)
        self.assertIn("Страниц без текста: 2", result)
        self.assertIn("не оценивалось", result)
        self.assertIn("визуальная полнота не подтверждена", result)


if __name__ == "__main__":
    unittest.main()

import unittest

from travel_recovery.cloud import cloud_safe_document, sync_and_answer


class CloudBoundaryTests(unittest.TestCase):
    def test_labeled_passport_is_never_cloud_safe(self):
        doc = {"kind": "passport", "pages": [{"text": "expiry only"}]}
        self.assertFalse(cloud_safe_document(doc))

    def test_unlabeled_identity_text_is_never_cloud_safe(self):
        doc = {"kind": "notes", "pages": [{"text": "Passport Number: X123\nDate of Birth: 2000-01-01"}]}
        self.assertFalse(cloud_safe_document(doc))

    def test_passport_question_is_blocked_before_any_cloud_call(self):
        with self.assertRaisesRegex(ValueError, "stay in local mode"):
            sync_and_answer({}, "When does my passport expire?", {"citations": []})


if __name__ == "__main__":
    unittest.main()

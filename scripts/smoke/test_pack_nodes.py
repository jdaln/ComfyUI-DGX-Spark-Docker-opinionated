#!/usr/bin/env python3
"""Unit tests for the template pack's own nodes, without a running server.

The pack's modules are imported one by one with light stand-ins for the ComfyUI
modules that would start CUDA or a server, so the tests run in seconds and use
no GPU. They need the container's Python for torch, Pillow and av:

    docker cp scripts/smoke/test_pack_nodes.py comfyui:/tmp/
    docker exec comfyui python3 /tmp/test_pack_nodes.py
"""
import importlib
import os
import re
import shutil
import sys
import tempfile
import types
import unittest

COMFY = "/workspace/ComfyUI"
PACK = os.path.join(COMFY, "custom_nodes", "ComfyUI-DGX-Spark-Templates")
WORK = "/tmp/pack_node_tests"


def stub_comfy():
    """Stand-ins for the ComfyUI modules the pack imports, enough for these tests."""
    import av
    import torch

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    def save_image_path(prefix, output_dir, width=0, height=0):
        subfolder, name = os.path.split(prefix)
        folder = os.path.join(output_dir, subfolder)
        os.makedirs(folder, exist_ok=True)
        counters = [int(m.group(1)) for f in os.listdir(folder) if (m := re.match(re.escape(name) + r"_(\d+)", f))]
        return folder, name, max(counters, default=0) + 1, subfolder, prefix

    def load(path):
        with av.open(path) as container:
            stream = container.streams.audio[0]
            frames = [torch.from_numpy(f.to_ndarray()) for f in container.decode(stream)]
        wave = torch.cat(frames, dim=1)
        return (wave.float() / 32768.0 if wave.dtype == torch.int16 else wave.float()), stream.sample_rate

    module("folder_paths", get_input_directory=lambda: os.path.join(WORK, "input"),
           get_output_directory=lambda: os.path.join(WORK, "output"), get_save_image_path=save_image_path)
    extras = module("comfy_extras")
    extras.nodes_audio = module("comfy_extras.nodes_audio", load=load)
    pack = module("pack")
    pack.__path__ = [PACK]


def load(name):
    return importlib.import_module(f"pack.{name}")


class SpeechText(unittest.TestCase):
    def test_numbers(self):
        text = load("speech_text_nodes")
        self.assertEqual(text.cardinal(2026), "kaksituhatta kaksikymmentäkuusi")
        self.assertEqual(text.cardinal(25, "iness"), "kahdessakymmenessäviidessä")
        self.assertEqual(text.cardinal(19, "part"), "yhdeksäätoista")
        self.assertEqual(text.cardinal(1000, "gen"), "tuhannen")
        self.assertEqual(text.ordinal(3, "iness"), "kolmannessa")
        self.assertEqual(text.ordinal(22), "kahdeskymmeneskahdes")
        self.assertEqual(text.ordinal(1000, "gen"), "tuhannennen")
        self.assertEqual(text.ordinal(2, "illat"), "toiseen")

    def test_speak_finnish(self):
        text = load("speech_text_nodes")
        cases = {
            "Bussi lähtee klo 9.30.": "Bussi lähtee kello yhdeksän kolmekymmentä.",
            "Kahvi maksaa 2,50 €.": "Kahvi maksaa kaksi euroa viisikymmentä senttiä.",
            "Tänään on 14.10.2026.": "Tänään on neljästoista lokakuuta kaksituhatta kaksikymmentäkuusi.",
            "Hän asuu 3. kerroksessa.": "Hän asuu kolmannessa kerroksessa.",
            "Ostin 12 omenaa ja 1,5 litraa maitoa.": "Ostin kaksitoista omenaa ja puolitoista litraa maitoa.",
            "Soita numeroon 040 123 4567.":
                "Soita numeroon nolla neljä nolla, yksi kaksi kolme, neljä viisi kuusi seitsemän.",
            "Juna on myöhässä n. 20 min.": "Juna on myöhässä noin kaksikymmentä minuuttia.",
            "Avoinna ma–pe 8–16.": "Avoinna maanantaista perjantaihin kahdeksasta kuuteentoista.",
            "Vuonna 1984 asuin 2 vuotta Turussa.":
                "Vuonna tuhat yhdeksänsataa kahdeksankymmentäneljä asuin kaksi vuotta Turussa.",
            "Esim. kahvi, tee jne. ovat hyviä.": "Esimerkiksi kahvi, tee ja niin edelleen ovat hyviä.",
            "Annoin 2 euron kolikon.": "Annoin kahden euron kolikon.",
            "Asun Kauppakatu 5:ssä.": "Asun Kauppakatu viidessä.",
            "Tule 4. päivänä.": "Tule neljäntenä päivänä.",
            "Toisesta kerroksesta 3. kerrokseen.": "Toisesta kerroksesta kolmanteen kerrokseen.",
            "Hän oli 3. ikkunan vieressä.": "Hän oli kolmannen ikkunan vieressä.",
            "N. 300 ihmistä tuli.": "Noin kolmesataa ihmistä tuli.",
            "Lämpötila on -5 °C.": "Lämpötila on miinus viisi astetta.",
            "Klo 8.00–16.00 auki.": "Kello kahdeksasta kuuteentoista auki.",
        }
        for written, spoken in cases.items():
            self.assertEqual(text.speak_finnish(written), spoken)
        node = text.FinnishNumbersToWords()
        self.assertEqual(node.convert("Kello 3", "English (en)"), ("Kello 3",))

    def test_split_sentences(self):
        text = load("speech_text_nodes")
        story = ('Bussi lähtee klo 9.30. Esim. kahvi maksaa 2,50 €. Hän asuu 3. kerroksessa, joka on korkealla! '
                 'Mitä kuuluu? "Hyvää," sanoi hän. Tri Virtanen tuli.\n\nToinen kappale, jossa on pilkku.')
        self.assertEqual(text.split_sentences(story, "fi"), [
            "Bussi lähtee klo 9.30.", "Esim. kahvi maksaa 2,50 €.", "Hän asuu 3. kerroksessa, joka on korkealla!",
            "Mitä kuuluu?", '"Hyvää," sanoi hän.', "Tri Virtanen tuli.", "Toinen kappale, jossa on pilkku."])
        chunks = text.split_sentences("Tämä on lause, jossa on pilkku, ja vielä yksi pilkku.", "fi", dictation=True)
        self.assertEqual(chunks, ["Tämä on lause,", "jossa on pilkku,", "ja vielä yksi pilkku."])
        long = " ".join(["sana"] * 120) + "."
        self.assertTrue(all(len(piece) <= 300 for piece in text.split_sentences(long, "fi")))
        self.assertEqual(text.language_code("Finnish (fi)"), "fi")

    def test_longest_speech(self):
        text = load("speech_text_nodes")
        self.assertAlmostEqual(text.longest_speech_seconds("Puhun suomea."), 2.0 + 0.2 * 13)
        self.assertAlmostEqual(text.longest_speech_seconds("你好"), 2.0 + 0.2 * 6)


class Characters(unittest.TestCase):
    def test_save_and_load(self):
        import torch
        nodes = load("character_nodes")
        picture = torch.rand(1, 64, 48, 3)
        voice = {"waveform": torch.sin(torch.linspace(0, 2000, 24000)).reshape(1, 1, -1) * 0.5, "sample_rate": 24000}
        nodes.SaveCharacter().save("Rouva  Virtanen", False, picture, voice)
        self.assertIn("Rouva Virtanen", nodes.characters())
        self.assertIs(nodes.LoadCharacter.VALIDATE_INPUTS("Rouva Virtanen"), True)
        loaded, sound, name = nodes.LoadCharacter().load("Rouva Virtanen")
        self.assertEqual(tuple(loaded.shape), (1, 64, 48, 3))
        self.assertEqual(sound["sample_rate"], 24000)
        self.assertAlmostEqual(sound["waveform"].shape[-1] / 24000, 1.0, places=2)
        self.assertEqual(name, "Rouva Virtanen")
        with self.assertRaisesRegex(ValueError, "already exists"):
            nodes.SaveCharacter().save("Rouva Virtanen", False, picture, voice)
        nodes.SaveCharacter().save("Rouva Virtanen", True, picture, voice)
        for bad in ("../x", "a/b", ".hidden", "", "x" * 61):
            with self.assertRaises(ValueError):
                nodes.character_path(bad, ".png")
        self.assertIsInstance(nodes.LoadCharacter.VALIDATE_INPUTS("Nobody"), str)


class Dialogue(unittest.TestCase):
    RATE = 24000

    def clip(self, seconds):
        import torch
        return {"waveform": torch.full((1, 1, round(seconds * self.RATE)), 0.1), "sample_rate": self.RATE}

    def test_join_with_pauses(self):
        nodes = load("dialogue_nodes")
        audio, lines = [self.clip(1.0), self.clip(2.0)], ["Hei.", "Mitä kuuluu?"]

        def join(mode, gap, twice=False):
            return nodes.JoinWithPauses().join([mode], [gap], [1.5], [twice], [0.3], [0.6], [True], audio, lines)

        _, srt, seconds = join("listen and repeat", 0.6)
        self.assertAlmostEqual(seconds, 0.3 + 1 + 1.5 + 2 + 3 + 0.6, places=3)
        self.assertEqual([round(start, 3) for start, _, _ in nodes.parse_srt(srt)], [0.3, 2.8])
        _, srt, seconds = join("dictation", 0.6)
        self.assertAlmostEqual(seconds, 0.3 + (1 + 0.6 + 1 + 1.5) + (2 + 0.6 + 2 + 3) + 0.6, places=3)
        self.assertEqual([text for _, _, text in nodes.parse_srt(srt)],
                         ["Hei.", "Hei.", "Mitä kuuluu?", "Mitä kuuluu?"])
        _, _, seconds = join("answer gap", 6.0)
        self.assertAlmostEqual(seconds, 0.3 + 1 + 6 + 2 + 6 + 0.6, places=3)
        _, _, seconds = join("fixed gap", 0.5)
        self.assertAlmostEqual(seconds, 0.3 + 1 + 0.5 + 2 + 0.6, places=3)
        with self.assertRaisesRegex(RuntimeError, "kept talking"):
            nodes.JoinWithPauses().join(["fixed gap"], [0.5], [1.5], [False], [0.3], [0.6], [True], [self.clip(9.0)],
                                        ["Hei."])

    def test_level_voices(self):
        import torch
        nodes = load("dialogue_nodes")
        time = torch.linspace(0, 1, self.RATE)
        loud = {"waveform": (0.9 * torch.sin(2000 * time)).reshape(1, 1, -1), "sample_rate": self.RATE}
        quiet = {"waveform": (0.05 * torch.sin(1500 * time)).reshape(1, 1, -1), "sample_rate": self.RATE}
        audio, srt, _ = nodes.JoinDialogue().join([loud, quiet], ["Hei.", "Moi."], ["A", "B"], [0.4], [0.3], [0.6],
                                                  [False])
        wave = audio["waveform"][0, 0]
        cues = [(round(s * self.RATE), round(e * self.RATE)) for s, e, _ in nodes.parse_srt(srt)]
        levels = [float(wave[s:e].pow(2).mean().sqrt()) for s, e in cues]
        self.assertAlmostEqual(levels[0], levels[1], delta=0.01)
        self.assertLess(float(wave.abs().max()), 0.9)
        audio, _, _ = nodes.JoinDialogue().join([loud, quiet], ["Hei.", "Moi."], ["A", "B"], [0.4], [0.3], [0.6],
                                                [False], [False])
        self.assertAlmostEqual(float(audio["waveform"].abs().max()), 0.9, places=3)

    def track(self, spans, total):
        import torch
        wave = torch.zeros(1, round(total * self.RATE))
        for start, end in spans:
            wave[:, round(start * self.RATE):round(end * self.RATE)] = 0.5
        srt = load("dialogue_nodes").format_srt([(s, e, f"repliikki {i + 1}") for i, (s, e) in enumerate(spans)])
        return {"waveform": wave.unsqueeze(0), "sample_rate": self.RATE}, srt

    def plan(self, shots, runs, spans, total, speakers=None, pictures=(), max_shot=15.0):
        nodes = load("dialogue_nodes")
        audio, srt = self.track(spans, total)
        return nodes.SegmentsFromSubtitles().plan(
            [shots], [runs], ["{picture} {speaker}: {line}"], ["{picture} listens"], ["Finnish"], [0.25], [0.25],
            [2.0], [max_shot], [audio], [srt], speakers, *[[p] for p in pictures])

    def check_shots(self, out, first_grid, later_grid):
        """Every shot on H3's frame grid, starting and ending in silence; the track and the shots agree."""
        nodes = load("dialogue_nodes")
        track, srt, segment_seconds, prompt, seconds, count, clips, lengths, prompts, _ = out
        frames = [round(length * 24) for length in lengths]
        self.assertEqual([round(float(s) * 24) for s in segment_seconds.split(",")], frames)
        self.assertEqual(frames[0] % 17, first_grid)
        self.assertTrue(all(f % 17 == later_grid for f in frames[1:]), frames)
        per_frame = self.RATE // 24
        wave = track["waveform"][0, 0]
        self.assertEqual(wave.shape[-1], sum(frames) * per_frame)
        self.assertAlmostEqual(seconds, sum(frames) / 24, places=6)
        quiet = round(0.25 * self.RATE) - 1
        for k, (f, clip) in enumerate(zip(frames, clips)):
            samples = clip["waveform"][0, 0]
            self.assertEqual(samples.shape[-1], f * per_frame)
            if k:  # the first shot starts with the track's own lead-in
                self.assertEqual(float(samples[:quiet].abs().max()), 0.0)
            self.assertEqual(float(samples[-quiet:].abs().max()), 0.0)
        ms = self.RATE // 1000 + 1  # SRT keeps milliseconds
        for start, end, _ in nodes.parse_srt(srt):
            first, last = round(start * self.RATE), round(end * self.RATE)
            self.assertEqual(float(wave[first + ms]), 0.5)
            self.assertEqual(float(wave[first - ms]), 0.0)
            self.assertEqual(float(wave[last - ms]), 0.5)
            self.assertEqual(float(wave[last + ms]), 0.0)
        self.assertEqual(prompt.split("\n---\n"), prompts)
        self.assertEqual(count, len(frames))
        return frames, nodes.parse_srt(srt), prompts

    def test_turns_one_run_per_shot(self):
        import torch
        aino, mikko = torch.full((1, 8, 8, 3), 0.2), torch.full((1, 8, 8, 3), 0.8)
        spans = [(0.3, 3.1), (3.5, 6.0), (6.4, 8.2), (8.3, 9.0)]
        out = self.plan("turns", "one run per shot", spans, 9.6, ["Aino", "Mikko", "Aino", "Aino"], (aino, mikko))
        frames, cues, prompts = self.check_shots(out, 5, 5)
        self.assertEqual(len(frames), 3)  # Aino's last two lines are one turn
        self.assertEqual(prompts[1], "<Picture 2> Mikko: repliikki 2")
        self.assertEqual(prompts[2], "<Picture 1> Aino: repliikki 3 repliikki 4")
        self.assertEqual([round(float(p.mean()), 3) for p in out[-1]], [0.2, 0.8, 0.2])
        for (start, end, _), (s, e) in zip(cues, spans):
            self.assertAlmostEqual(end - start, e - s, places=3)
        self.assertAlmostEqual(cues[3][0] - cues[2][1], 0.1, places=3)  # a pause inside a turn is kept as it is
        split = self.plan("turns", "one run per shot", spans, 9.6, ["Aino", "Mikko", "Aino", "Aino"], (aino, mikko),
                          max_shot=1.5)
        self.assertEqual(split[5], 4)
        with self.assertRaisesRegex(ValueError, "picture_2"):
            self.plan("turns", "one run per shot", spans, 9.6, ["Aino", "Mikko", "Aino", "Aino"], (aino,))

    def test_only_speakers_pictures_are_asked_for(self):
        node = load("dialogue_nodes").SegmentsFromSubtitles()
        args = [["turns"], ["one run per shot"], [""], [""], ["Finnish"], [0.25], [0.25], [2.0], [15.0], [None], [""]]
        made = [object()]
        wanted = node.check_lazy_status(*args, speakers=["A", "B", "A"], picture_1=[None], picture_2=[None],
                                        picture_3=[None], picture_4=[None])
        self.assertEqual(wanted, ["picture_1", "picture_2"])
        self.assertEqual(node.check_lazy_status(*args, speakers=["A", "B"], picture_1=made, picture_2=[None]),
                         ["picture_2"])
        self.assertEqual(node.check_lazy_status(*args, picture_1=[None], picture_2=[None]), ["picture_1"])
        self.assertEqual(node.check_lazy_status(*args, speakers=["A", "B"]), [])

    def test_short_pause_is_lengthened(self):
        spans = [(0.3, 2.0), (2.1, 4.0)]
        frames, cues, _ = self.check_shots(self.plan("turns", "one run per shot", spans, 4.5, ["A", "B"]), 5, 5)
        self.assertGreaterEqual(cues[1][0] - cues[0][1], 0.5 - 1e-3)

    def test_speak_and_listen_context_segments(self):
        spans = [(0.2, 2.0), (8.0, 9.5)]
        out = self.plan("speak and listen", "context segments", spans, 15.5, ["Aino", "Aino"])
        frames, cues, prompts = self.check_shots(out, 5, 0)
        self.assertEqual(prompts, ["<Picture 1> Aino: repliikki 1", "<Picture 1> listens",
                                   "<Picture 1> Aino: repliikki 2", "<Picture 1> listens"])
        self.assertGreaterEqual(cues[1][0] - cues[0][1], 6.0 - 1e-3)  # the answer time is never shortened
        self.assertGreaterEqual(sum(frames) / 24, 15.5 - 1e-3)


if __name__ == "__main__":
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(os.path.join(WORK, "input"))
    tempfile.tempdir = WORK
    stub_comfy()
    unittest.main(verbosity=2)

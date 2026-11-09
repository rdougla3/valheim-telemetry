"""Regression checks for source publication and configurable deployments."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import app
import config

ROOT = Path(__file__).resolve().parent.parent


class PublicationTests(unittest.TestCase):
    def test_new_database_has_no_seeded_player_records(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'DATA', Path(tmp)), patch.object(app, 'DB', Path(tmp)/'test.sqlite3'):
            app.initialize()
            with app.connect() as db:
                for table in ('identities', 'sessions', 'events'):
                    self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_local_config_parsing_is_literal_and_handles_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'.env'
            path.write_text("SERVER_NAME='A realm with spaces'\nSERVER_PASSWORD='literal $value # text'\nDASHBOARD_TITLE=''\n")
            values = config.read_env(path)
            self.assertEqual(values['SERVER_NAME'], 'A realm with spaces')
            self.assertEqual(values['SERVER_PASSWORD'], 'literal $value # text')
            self.assertEqual(values['DASHBOARD_TITLE'], '')
            path.write_text('NAME=unquoted spaces\n')
            with self.assertRaises(ValueError): config.read_env(path)

    def test_public_display_is_escaped_and_never_includes_password(self):
        page = (ROOT/'valheim-dashboard/static/index.html').read_bytes()
        display = {k: 'Example Realm' for k in config.PUBLIC_DISPLAY}
        display['DASHBOARD_TITLE'] = '<script>alert("example")</script>'
        with patch.dict(os.environ, {'SERVER_PASSWORD': 'private-fixture-password'}), patch.object(config, 'PUBLIC_DISPLAY', display):
            rendered = app.render_index(page).decode()
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>alert', rendered)
        self.assertNotIn('private-fixture-password', rendered)
        self.assertNotIn('{{DASHBOARD_', rendered)

    def test_journal_filters_use_configured_container_uid_and_unit(self):
        with patch.dict(os.environ, {'VALHEIM_JOURNAL_CONTAINER':'example-game', 'VALHEIM_JOURNAL_USER_UNIT':'example-game.service', 'RUN_UID':'4242'}):
            args=app.journal_command()
        self.assertIn('CONTAINER_NAME=example-game', args)
        self.assertIn('_UID=4242', args)
        self.assertIn('_SYSTEMD_USER_UNIT=example-game.service', args)

    def test_start_script_preserves_quoted_settings_as_separate_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); server=root/'server'; server.mkdir()
            for name in ['BepInEx/core/BepInEx.Preloader.dll','BepInEx/plugins/ValheimObserver/ValheimObserver.dll']:
                p=server/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
            fake=server/'valheim_server.x86_64'
            fake.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["ARGUMENT_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))\n')
            fake.chmod(0o755)
            env=dict(os.environ, VALHEIM_ENV_FILE=str(root/'missing.env'), VALHEIM_SERVER_DIR=str(server), VALHEIM_SAVE_DIR=str(root/'saves'), VALHEIM_LIVE_PATH=str(root/'dashboard/live.json'), SERVER_NAME='Example "quoted" Realm', WORLD_NAME='ExampleWorld', SERVER_PASSWORD='private-fixture-password', SERVER_PORT='2540', SERVER_PUBLIC='0', HOME=str(root/'home'), ARGUMENT_CAPTURE=str(root/'args.json'))
            for crossplay in ['true','false']:
                env['CROSSPLAY']=crossplay
                subprocess.run(['bash',str(ROOT/'valheim/start-server.sh')],env=env,capture_output=True,text=True,check=True)
                args=json.loads((root/'args.json').read_text())
                self.assertEqual(args[args.index('-name')+1],env['SERVER_NAME'])
                self.assertEqual(args[args.index('-password')+1],env['SERVER_PASSWORD'])
                self.assertEqual('-crossplay' in args,crossplay=='true')
            env['SERVER_PASSWORD']='CHANGE_ME_BEFORE_STARTING'
            self.assertNotEqual(subprocess.run(['bash',str(ROOT/'valheim/start-server.sh')],env=env,capture_output=True).returncode,0)

    def test_git_ignores_runtime_data_and_secrets_but_keeps_examples(self):
        if not (ROOT/'.git').exists(): self.skipTest('requires a Git checkout')
        private=['.env','valheim/server.env','valheim/data/worlds_local/ExampleWorld/_main.1.fwl2','valheim/server/BepInEx/config/local.cfg','valheim-dashboard/data/dashboard.sqlite3','valheim-dashboard/data/telemetry/test.jsonl','valheim/backups/private.tar.gz','valheim-dashboard/plugin/bin/Release/ValheimObserver.dll']
        for name in private:
            result=subprocess.run(['git','check-ignore','--no-index','-q',name],cwd=ROOT)
            self.assertEqual(result.returncode,0,name)
        self.assertEqual(subprocess.run(['git','check-ignore','--no-index','-q','.env.example'],cwd=ROOT).returncode,1)


if __name__ == '__main__': unittest.main()

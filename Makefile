# CMDB Syncer DEV
# .local.

cp-up:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml build
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool up --force-recreate -d

cp-start:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool start

cp-stop:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool stop

cp-down:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool down --rmi local --remove-orphans

logs:
	docker logs -f eventtool_application_1


shell:
	docker exec -it eventtool_application_1 sh

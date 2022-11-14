# CMDB Syncer DEV
# .local.

up:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml build
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool up --force-recreate -d

start:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool start

stop:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool stop

down:
	docker-compose -f docker-compose.yml -f docker-compose.local.yml -p eventtool down --rmi local --remove-orphans

logs:
	docker logs -f eventtool-application-1


shell:
	docker exec -it eventtool-application-1 sh
